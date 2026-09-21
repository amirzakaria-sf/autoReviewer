#!/usr/bin/env python3
"""Generate the PWA icon set. No dependencies -- writes PNG bytes directly.

Committed output, reproducible input. The alternative was either adding
Pillow to a project that has no other use for it, or checking in binaries
nobody can regenerate when the accent colour changes.

The mark is the shield from the header, drawn at 4x and box-filtered down, so
the curve is smooth without a rasteriser. Amber on warm graphite, the two
colours the product is already built from (globals.css).

    python3 scripts/make-icons.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

GROUND = (8, 9, 10)        # --ink-900
AMBER = (255, 178, 36)     # --amber
AMBER_DEEP = (122, 85, 16)  # --amber-dim
SS = 4                      # supersampling factor

PUBLIC = Path(__file__).resolve().parent.parent / "public"


def write_png(path: Path, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
    """Minimal RGB PNG: one IHDR, one IDAT, one IEND."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for each scanline
        for x in range(width):
            raw.extend(pixels[y * width + x])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def shield_contains(x: float, y: float, size: float, scale: float = 1.0) -> bool:
    """Is (x, y) inside the shield, scaled about its own centre?

    A shield is a rectangle whose bottom third tapers to a point. Expressed as
    a half-width that shrinks with depth rather than as a polygon, so scaling
    it for the inner cut-out is one multiply.
    """
    cx = size / 2
    top = size * 0.16
    bottom = size * 0.86
    half = size * 0.30 * scale
    top = cx - (cx - top) * scale
    bottom = cx + (bottom - cx) * scale

    if y < top or y > bottom:
        return False

    depth = (y - top) / (bottom - top)
    if depth < 0.55:
        allowed = half
    else:
        # Taper to the point across the bottom 45%, eased so the shoulders
        # curve rather than breaking at a hard angle.
        t = (depth - 0.55) / 0.45
        allowed = half * (1 - t * t)

    # Round the top corners by the same easing, upside down.
    if depth < 0.12:
        t = 1 - depth / 0.12
        allowed *= 1 - 0.28 * t * t

    return abs(x - cx) <= allowed


def render(size: int, *, padded: bool) -> list[tuple[int, int, int]]:
    """`padded` leaves a margin so iOS's own rounded-rect mask cannot clip the
    mark -- Apple crops a home-screen icon to a squircle without asking."""
    big = size * SS
    inset = big * 0.14 if padded else 0.0
    span = big - inset * 2

    accumulator = [(0, 0, 0)] * (big * big)
    for y in range(big):
        for x in range(big):
            sx, sy = x - inset, y - inset
            if 0 <= sx < span and 0 <= sy < span and shield_contains(sx, sy, span):
                # A vertical ramp inside the shield: brighter at the top, so
                # the mark has some weight instead of reading as a flat sticker.
                t = sy / span
                colour = tuple(
                    round(AMBER[i] + (AMBER_DEEP[i] - AMBER[i]) * min(1.0, t * 1.15)) for i in range(3)
                )
                if shield_contains(sx, sy, span, scale=0.62):
                    colour = GROUND  # the hollow centre
            else:
                colour = GROUND
            accumulator[y * big + x] = colour

    # Box-filter down: this is the anti-aliasing.
    out: list[tuple[int, int, int]] = []
    for y in range(size):
        for x in range(size):
            r = g = b = 0
            for dy in range(SS):
                for dx in range(SS):
                    pr, pg, pb = accumulator[(y * SS + dy) * big + (x * SS + dx)]
                    r += pr
                    g += pg
                    b += pb
            n = SS * SS
            out.append((r // n, g // n, b // n))
    return out


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    for name, size, padded in [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        # Maskable icons are cropped to whatever shape the launcher likes, so
        # the mark has to sit inside the safe zone.
        ("icon-maskable-512.png", 512, True),
        ("apple-touch-icon.png", 180, True),
        ("icon.png", 64, False),
    ]:
        write_png(PUBLIC / name, size, size, render(size, padded=padded))
        print(f"wrote public/{name} ({size}x{size})")


if __name__ == "__main__":
    main()
