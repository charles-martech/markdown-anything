"""Draw the app icon and pack it into macOS .icns, Windows .ico and a PNG.

Run this only when changing the artwork:  python3 macos/make_icon.py
It writes macos/icon.icns (used by the app bundle), windows/icon.ico (used by
the Windows shortcuts) and docs/icon.png (used by the README and the Linux
menu entry). Requires Pillow; nothing at runtime does.
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SIZE = 1024
INK = (255, 255, 255, 255)
TOP = (86, 96, 240)       # indigo
BOTTOM = (146, 84, 222)   # violet
PAGE = (255, 255, 255, 255)
FOLD = (216, 220, 255, 255)


def rounded_gradient(size: int) -> Image.Image:
    """macOS-style squircle with a soft vertical gradient."""
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        gradient.putpixel((0, y), tuple(
            round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * 0.225), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(gradient, (0, 0), mask)
    return canvas


def draw_document(canvas: Image.Image) -> None:
    """A page with a folded corner, and the markdown down-arrow on it."""
    scale = 4
    layer = Image.new("RGBA", (SIZE * scale, SIZE * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    s = lambda v: int(v * SIZE * scale)  # noqa: E731 - fraction of the canvas

    left, right = s(0.255), s(0.745)
    top, bottom = s(0.175), s(0.825)
    fold = s(0.17)

    draw.rounded_rectangle((left, top, right, bottom), radius=s(0.045), fill=PAGE)
    # Clip the top-right corner, then lay the fold triangle over it.
    draw.polygon([(right - fold, top - s(0.01)), (right + s(0.01), top - s(0.01)),
                  (right + s(0.01), top + fold)], fill=(0, 0, 0, 0))
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)],
                 fill=FOLD)
    draw.line([(right - fold, top), (right - fold, top + fold), (right, top + fold)],
              fill=(190, 196, 245, 255), width=s(0.006))

    # Two text lines, left aligned and clear of the fold, so the shape reads
    # as a document rather than a download badge.
    text_left = left + s(0.055)
    for index, width in enumerate((0.24, 0.17)):
        y = s(0.29) + index * s(0.070)
        draw.rounded_rectangle((text_left, y, text_left + s(width), y + s(0.034)),
                               radius=s(0.017), fill=(206, 212, 246, 255))

    # The markdown mark: a down arrow, the one glyph everyone reads as "to md".
    cx = (left + right) // 2
    shaft_top, shaft_bottom = s(0.455), s(0.615)
    shaft = s(0.044)
    draw.rounded_rectangle((cx - shaft, shaft_top, cx + shaft, shaft_bottom),
                           radius=s(0.012), fill=TOP + (255,))
    head = s(0.125)
    draw.polygon([(cx - head, shaft_bottom), (cx + head, shaft_bottom),
                  (cx, shaft_bottom + s(0.115))], fill=TOP + (255,))

    canvas.alpha_composite(layer.resize((SIZE, SIZE), Image.LANCZOS))


def build_icns(master: Image.Image, path: Path) -> None:
    """Pack PNG variants into an .icns container."""
    variants = [
        (b"icp4", 16), (b"icp5", 32), (b"ic11", 32), (b"ic12", 64),
        (b"ic07", 128), (b"ic13", 256), (b"ic08", 256), (b"ic14", 512),
        (b"ic09", 512), (b"ic10", 1024),
    ]
    chunks = b""
    for kind, size in variants:
        buffer = io.BytesIO()
        master.resize((size, size), Image.LANCZOS).save(buffer, format="PNG")
        data = buffer.getvalue()
        chunks += kind + struct.pack(">I", len(data) + 8) + data
    path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + chunks)


def main() -> None:
    icon = rounded_gradient(SIZE)
    draw_document(icon)
    build_icns(icon, HERE / "icon.icns")
    icon.resize((512, 512), Image.LANCZOS).save(HERE.parent / "docs" / "icon.png")
    ico = HERE.parent / "windows" / "icon.ico"
    ico.parent.mkdir(exist_ok=True)
    icon.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                          (128, 128), (256, 256)])
    print(f"wrote {HERE / 'icon.icns'}, windows/icon.ico and docs/icon.png")


if __name__ == "__main__":
    main()
