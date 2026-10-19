"""Run the watermark engine locally, without Telegram — handy for trying settings.

    python -m wmbot.cli input.mp4 --text "@MyChannel" --set preset=veryfast --set bg_enabled=true
    python -m wmbot.cli book.pdf  --text "CONFIDENTIAL" --set mode=tile
    python -m wmbot.cli --list-settings
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import threading
import time
from pathlib import Path

from .imgmark import IMAGE_EXTS, watermark_image
from .pdfmark import watermark_pdf
from .render import load_logo
from .settings import FIELDS, FIELD_MAP, STYLE_PRESETS, WMSettings, apply_style_preset
from .video import VIDEO_EXTS, watermark_video


def _coerce(raw: str):
    low = raw.lower()
    return True if low in ("true", "yes", "on") else False if low in ("false", "no", "off") else raw


async def run(args) -> int:
    if args.list_settings:
        for f in FIELDS:
            extra = f"  choices: {', '.join(c for c, _ in f.choices)}" if f.choices else (f"  range {f.lo:g}–{f.hi:g}" if f.kind in ("num", "int") else "")
            print(f"{f.key:18} {f.kind:7} {f.label}{extra}")
        print("\nstyles:", ", ".join(STYLE_PRESETS))
        return 0
    if not args.input:
        print("give an input file (or --list-settings)")
        return 2
    s = WMSettings()
    if args.style and not apply_style_preset(s, args.style):
        print(f"unknown style {args.style!r}")
        return 2
    if args.text:
        s.set("text", args.text)
        s.set("text_enabled", True)
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        if k not in FIELD_MAP or not s.set(k, _coerce(v)):
            print(f"bad setting: {kv}   (see --list-settings)")
            return 2
    logo = None
    if args.logo:
        logo = load_logo(args.logo)
        s.set("logo_enabled", logo is not None)
    src = Path(args.input)
    out_dir = Path(args.out or ".").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem + "_wm"
    t0 = time.time()
    ext = src.suffix.lower()
    if ext == ".pdf":
        dst = out_dir / (stem + ".pdf")
        r = await asyncio.to_thread(watermark_pdf, src, dst, s, logo, threading.Event())
        print(f"✅ {r['pages']}/{r['total']} pages → {dst}")
    elif ext in IMAGE_EXTS:
        dst = out_dir / (stem + ext)
        await asyncio.to_thread(watermark_image, src, dst, s, logo)
        print(f"✅ {dst}")
    elif ext in VIDEO_EXTS:
        last = [0.0]

        async def cb(p):
            if time.time() - last[0] > 1:
                last[0] = time.time()
                print(f"\r  {p.fraction * 100:5.1f}%", end="", flush=True)
        dst, info = await watermark_video(src, out_dir, s, logo, asyncio.Event(), stem, cb)
        print(f"\r✅ {info.width}x{info.height} {info.duration:.1f}s → {dst}")
    else:
        print("unsupported file type")
        return 2
    print(f"   took {time.time() - t0:.1f}s")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?")
    ap.add_argument("--text")
    ap.add_argument("--logo")
    ap.add_argument("--style", help="preset name")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE")
    ap.add_argument("--out", help="output folder (default: current)")
    ap.add_argument("--list-settings", action="store_true")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
