"""Generate the /start loading animation (assets/banners/loading.gif).

Faithful port of the "Rose Three" math-curve loader supplied for the bot
(r = a cos(3t) with a 76-particle fading trail), rendered as a real Telegram
animation (GIF) because Telegram cannot run the original HTML/JS loader.
Run manually after changing the design:  python scripts/make_loader_gif.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 240                 # px, square
FRAMES = 48                # 48 x 50 ms = 2.4 s seamless loop
FRAME_MS = 50
PARTICLES = 76
TRAIL_SPAN = 0.31
DURATION_MS = 5300
PULSE_MS = 4400
ROTATION_MS = 28000
BG = (5, 5, 5)
INK = (255, 255, 255)

OUT = Path(__file__).resolve().parent.parent / "assets" / "banners" / "loading.gif"


def _detail_scale(time_ms: float) -> float:
    pulse = (time_ms % PULSE_MS) / PULSE_MS
    return 0.52 + ((math.sin(pulse * math.tau + 0.55) + 1) / 2) * 0.48


def _point(progress: float, s: float) -> tuple[float, float]:
    t = progress * math.tau
    a = 9.2 + 0.60 * s
    r = a * (0.72 + 0.28 * s) * math.cos(3 * t)
    return 50 + math.cos(t) * r * 3.25, 50 + math.sin(t) * r * 3.25


def _frame(index: int) -> Image.Image:
    time_ms = index * FRAME_MS
    progress = (time_ms % DURATION_MS) / DURATION_MS
    s = _detail_scale(time_ms)
    scale = SIZE / 100.0
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img, "RGBA")

    def xy(p: tuple[float, float]) -> tuple[float, float]:
        # rotation of the whole group: -360deg over ROTATION_MS, around centre
        ang = -((time_ms % ROTATION_MS) / ROTATION_MS) * math.tau
        dx, dy = p[0] - 50, p[1] - 50
        ca, sa = math.cos(ang), math.sin(ang)
        return (50 + dx * ca - dy * sa) * scale, (50 + dx * sa + dy * ca) * scale

    # faint full curve underneath (opacity ~0.1 in the original)
    pts = [xy(_point(i / 240, s)) for i in range(241)]
    d.line(pts, fill=(255, 255, 255, 26), width=max(2, int(4.6 * scale * 0.45)))

    for i in range(PARTICLES):
        tail = i / (PARTICLES - 1)
        fade = (1 - tail) ** 0.56
        p = ((progress - tail * TRAIL_SPAN) % 1.0)
        x, y = xy(_point(p, s))
        r = (0.9 + fade * 2.7) * scale * 0.5
        alpha = int(255 * (0.04 + fade * 0.96))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*INK, alpha))
    return img


def main() -> None:
    palette_img = Image.new("P", (1, 1))
    palette_img.putpalette([v for i in range(256) for v in (i, i, i)])
    frames = [_frame(i).quantize(palette=palette_img, dither=Image.Dither.NONE)
              for i in range(FRAMES)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT, save_all=True, append_images=frames[1:],
                   duration=FRAME_MS, loop=0, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes, {FRAMES} frames)")


if __name__ == "__main__":
    main()
