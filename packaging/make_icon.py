"""Draws the app icon (a white microphone on a blue-violet squircle) as a macOS .iconset folder."""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024


def draw_icon() -> Image.Image:
    scale = 4  # draw big, then downsample, for smooth edges
    s = SIZE * scale
    gradient = Image.new("RGB", (s, s))
    top, bottom = (64, 140, 255), (124, 77, 255)
    pixels = gradient.load()
    for y in range(s):
        t = y / (s - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
        for x in range(s):
            pixels[x, y] = color

    mask = Image.new("L", (s, s), 0)
    margin = round(s * 0.09)  # Apple's icon grid leaves room around the squircle
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, s - margin, s - margin), radius=round(s * 0.2), fill=255)
    icon = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    icon.paste(gradient, mask=mask)

    d = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    cx = s / 2
    w, top_y, bottom_y = s * 0.17, s * 0.24, s * 0.56  # the microphone capsule
    d.rounded_rectangle((cx - w / 2, top_y, cx + w / 2, bottom_y), radius=w / 2, fill=white)
    stroke = round(s * 0.035)
    arc_w = s * 0.30  # the holder arc around the capsule
    d.arc((cx - arc_w / 2, s * 0.33, cx + arc_w / 2, s * 0.64), start=0, end=180, fill=white, width=stroke)
    d.line((cx, s * 0.64, cx, s * 0.73), fill=white, width=stroke)  # stand
    d.rounded_rectangle((cx - s * 0.1, s * 0.72, cx + s * 0.1, s * 0.72 + stroke), radius=stroke / 2, fill=white)
    return icon.resize((SIZE, SIZE), Image.LANCZOS)


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    for size in (16, 32, 128, 256, 512):
        icon.resize((size, size), Image.LANCZOS).save(out / f"icon_{size}x{size}.png")
        icon.resize((size * 2, size * 2), Image.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "build/icon.iconset")
