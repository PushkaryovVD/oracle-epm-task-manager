"""Generate assets/app.ico (multi-size, PNG-in-ICO) using only the Python standard library.

A blue rounded square with a white calendar sheet and a check mark.
Usage (from the project root):  .venv/Scripts/python.exe tools/make_icon.py
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 32, 48, 256)
SUBSAMPLES = 4  # per axis, for anti-aliased edges

BLUE = (31, 95, 173, 255)
DARK_BLUE = (18, 62, 122, 255)
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def _in_rounded_rect(u: float, v: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= u <= x1 and y0 <= v <= y1):
        return False
    cx = min(max(u, x0 + r), x1 - r)
    cy = min(max(v, y0 + r), y1 - r)
    return (u - cx) ** 2 + (v - cy) ** 2 <= r * r


def _in_segment(u: float, v: float, ax: float, ay: float, bx: float, by: float, half: float) -> bool:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((u - ax) * dx + (v - ay) * dy) / (dx * dx + dy * dy)))
    px, py = ax + t * dx - u, ay + t * dy - v
    return px * px + py * py <= half * half


def _sample(u: float, v: float) -> tuple[int, int, int, int]:
    """Colour of the icon at normalised point (u, v) in [0, 1]."""
    if not _in_rounded_rect(u, v, 0.02, 0.02, 0.98, 0.98, 0.22):
        return CLEAR
    # calendar sheet
    if _in_rounded_rect(u, v, 0.20, 0.26, 0.80, 0.84, 0.07):
        if v < 0.40:  # header band
            return DARK_BLUE
        if _in_segment(u, v, 0.35, 0.60, 0.46, 0.71, 0.045) or _in_segment(u, v, 0.46, 0.71, 0.67, 0.47, 0.045):
            return BLUE  # check mark
        return WHITE
    # binder rings
    for cx in (0.36, 0.64):
        if abs(u - cx) <= 0.035 and 0.16 <= v <= 0.32:
            return WHITE
    return BLUE


def _pixel_rgba(px: int, py: int, size: int) -> tuple[int, int, int, int]:
    acc = [0, 0, 0, 0]
    n = SUBSAMPLES * SUBSAMPLES
    for sy in range(SUBSAMPLES):
        for sx in range(SUBSAMPLES):
            u = (px + (sx + 0.5) / SUBSAMPLES) / size
            v = (py + (sy + 0.5) / SUBSAMPLES) / size
            c = _sample(u, v)
            for i in range(4):
                acc[i] += c[i]
    return tuple(a // n for a in acc)  # type: ignore[return-value]


def _png(size: int) -> bytes:
    rows = bytearray()
    for py in range(size):
        rows.append(0)  # filter type: none
        for px in range(size):
            rows.extend(_pixel_rgba(px, py, size))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def build_ico(path: Path) -> None:
    images = [(size, _png(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    blobs = b""
    for size, data in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = root / "assets" / "app.ico"
    build_ico(target)
    print(f"Wrote {target} ({target.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
