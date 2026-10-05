#!/usr/bin/env python3
"""A tiny, fast, offline image generator for demoing `generate_image`.

It deterministically produces a symmetric pixel-art "sprite" from a text
prompt — no model download, generates in well under a second — so you can prove
the local image-generation pipeline end to end (including inside ChatGPT, which
caps tool calls at ~45s).

Swap this for a real text-to-image generator (mflux, stable-diffusion.cpp, …)
by pointing --image-gen-cmd at that tool instead; the contract is the same:

    <generator> ... {prompt} ... {output}

Usage:
    pixel_gen.py "<prompt>" <output.png>

Requires: pillow
"""

from __future__ import annotations

import hashlib
import sys

from PIL import Image


def generate(prompt: str, out_path: str) -> None:
    # Seed everything from the prompt so the same prompt → the same sprite.
    digest = hashlib.sha256(prompt.encode("utf-8")).digest()

    grid = 16  # logical pixels per side
    scale = 24  # upscale factor → 384x384 output
    half = grid // 2

    # Pick a small palette from the hash.
    def color(i: int) -> tuple[int, int, int]:
        b = digest[i % len(digest)], digest[(i * 7 + 3) % len(digest)], digest[
            (i * 13 + 5) % len(digest)
        ]
        # Push toward vivid colors.
        return tuple(80 + (c % 176) for c in b)  # type: ignore[return-value]

    body = color(1)
    accent = color(9)

    img = Image.new("RGBA", (grid, grid), (0, 0, 0, 0))
    px = img.load()

    # Fill the left half from hash bits, mirror to the right → symmetry.
    bits = int.from_bytes(digest, "big")
    for y in range(grid):
        for x in range(half):
            idx = y * half + x
            on = (bits >> (idx % 256)) & 1
            if not on:
                continue
            c = accent if ((bits >> ((idx * 3) % 256)) & 1) else body
            px[x, y] = (*c, 255)
            px[grid - 1 - x, y] = (*c, 255)

    img = img.resize((grid * scale, grid * scale), Image.NEAREST)
    img.save(out_path)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: pixel_gen.py '<prompt>' <output.png>", file=sys.stderr)
        return 2
    generate(sys.argv[1], sys.argv[2])
    print(f"wrote {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
