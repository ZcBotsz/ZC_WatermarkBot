"""
Perfect Watermark Engine - Balanced, Not Too Much, Not Too Less
Features: txt + bg opacity + border + crf + size + all from screenshots
Video: FFmpeg ultrafast + c:a copy + scale2ref + drawtext 8 modes
Image: PIL rounded box + border + shadow + logo + Hindi Unicode
PDF: PyMuPDF diagonal/tiled/center + bg + border + logo
Speed: GPU detect, threads, faststart, progress file
"""
import os
import re
import math
import asyncio
import tempfile
import subprocess
from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw, ImageFont
import fitz

from .config import Config, Positions
from .utils import escape_ffmpeg, probe_video, hex_to_rgb, find_font, detect_gpu

class Engine:
    def __init__(self):
        self.gpu = detect_gpu()
        print(f"[Engine] GPU: {self.gpu}")

    # ---------- VIDEO FILTERS ----------
    def _text_filter(self, cfg: Dict, vinfo: Dict) -> str:
        W = vinfo["width"]
        H = vinfo["height"]
        fps = vinfo.get("fps", 30)
        raw_text = cfg.get("text", "@MyChannel")
        # Handle multiline: take first line for video (FFmpeg drawtext doesn't like \n well)
        text = escape_ffmpeg(raw_text.split("\n")[0][:100])
        fs = cfg.get("font_size", 36)
        opacity = cfg.get("opacity", 0.8)
        color = cfg.get("color", "white")
        bg_enabled = cfg.get("bg_enabled", True)
        bg_color = cfg.get("bg_color", "black")
        bg_op = cfg.get("bg_opacity", 0.5)
        border = cfg.get("border", True)
        border_w = cfg.get("border_width", 3)
        shadow = cfg.get("shadow", True)
        speed = cfg.get("speed", 2.0)
        mode = cfg.get("mode", "bouncing")
        pos = cfg.get("position", "bottom_right")
        margin = 20

        fontcolor = f"{color}@{opacity}"
        box = 1 if bg_enabled else 0
        boxcolor = f"{bg_color}@{bg_op}"
        boxborder = border_w if border else 0

        def static_xy():
            return Positions.ffmpeg_pos(pos)

        filters = []

        def add_shadow(x, y):
            if shadow:
                filters.append(f"drawtext=text='{text}':fontcolor=black@0.4:fontsize={fs}:x=({x})+2:y=({y})+2")

        if mode == "static":
            x,y = static_xy()
            add_shadow(x,y)
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}:box={box}:boxcolor={boxcolor}:boxborderw={boxborder}")

        elif mode == "bouncing":
            # DVD bounce - best from screenshots
            tw = fs * len(raw_text) * 0.6
            th = fs * 1.2
            pw = max(1, W - tw)
            ph = max(1, H - th)
            x = f"abs(mod(t*{speed*50:.0f},{2*pw:.0f})-{pw:.0f})"
            y = f"abs(mod(t*{speed*35:.0f},{2*ph:.0f})-{ph:.0f})"
            add_shadow(x,y)
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}:box={box}:boxcolor={boxcolor}:boxborderw={boxborder}")

        elif mode == "floating":
            cx, cy = W//2, H//2
            x = f"{cx}+30*sin(2*PI*0.05*t)"
            y = f"{cy}+30*cos(2*PI*0.05*1.3*t)"
            add_shadow(x,y)
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}:box={box}:boxcolor={boxcolor}:boxborderw={boxborder}")

        elif mode == "scrolling":
            y = H - fs*2 - margin
            x = f"{W}-mod(t*{speed*80:.0f},{W+600})"
            filters.append(f"drawbox=x=0:y={y-10}:w={W}:h={fs+20}:color={bg_color}@{bg_op}:t=fill")
            add_shadow(x, str(y))
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}")

        elif mode == "diagonal":
            x = f"mod(t*{speed*40:.0f},{W+200})-100"
            y = f"mod(t*{speed*25:.0f},{H+100})-50"
            add_shadow(x,y)
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}:box={box}:boxcolor={boxcolor}:boxborderw={boxborder}:angle=45*PI/180")

        elif mode == "tiled":
            # 12 tiles max for speed
            sx, sy = 250, 180
            cols = min(4, (W//sx)+1)
            rows = min(3, (H//sy)+1)
            for r in range(rows):
                for c in range(cols):
                    tx = c*sx
                    ty = r*sy
                    filters.append(f"drawtext=text='{text}':fontcolor={color}@{opacity*0.4}:fontsize={fs//2}:x={tx}:y={ty}:angle=-30*PI/180")
            if not filters:
                x,y = static_xy()
                filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}")

        elif mode == "transparent":
            x,y = static_xy()
            filters.append(f"drawtext=text='{text}':fontcolor={color}@{min(0.3, opacity)}:fontsize={fs}:x={x}:y={y}")

        elif mode == "cinematic":
            bar_h = int(H*0.1)
            filters.append(f"drawbox=x=0:y=0:w={W}:h={bar_h}:color=black@0.85:t=fill")
            filters.append(f"drawbox=x=0:y={H-bar_h}:w={W}:h={bar_h}:color=black@0.85:t=fill")
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x=(w-text_w)/2:y={H-bar_h+10}")

        else:
            x,y = static_xy()
            add_shadow(x,y)
            filters.append(f"drawtext=text='{text}':fontcolor={fontcolor}:fontsize={fs}:x={x}:y={y}:box={box}:boxcolor={boxcolor}:boxborderw={boxborder}")

        return ",".join(filters)

    async def video(self, inp: str, out: str, cfg: Dict, cb=None) -> bool:
        import psutil
        vinfo = probe_video(inp)
        W,H = vinfo["width"], vinfo["height"]
        logo = cfg.get("logo_path")
        has_logo = logo and os.path.exists(logo)

        # base cmd
        cmd = ["ffmpeg", "-y", "-hide_banner"]

        # progress file
        prog_file = tempfile.mktemp(suffix=".txt")
        cmd += ["-progress", prog_file, "-loglevel", "error"]

        # inputs
        if has_logo:
            cmd += ["-i", logo, "-i", inp]
        else:
            cmd += ["-i", inp]

        # filters
        preset = cfg.get("preset", "ultrafast")
        crf = cfg.get("crf", 23)
        res = cfg.get("resolution", "original")
        codec = cfg.get("codec", "h264")
        pos = cfg.get("position", "bottom_right")
        logo_size = cfg.get("logo_size", 15)
        logo_op = cfg.get("logo_opacity", 0.8)

        # resolution
        target_h = None
        if res != "original":
            target_h = {"1080p":1080,"720p":720,"540p":540,"480p":480,"360p":360}.get(res)

        # build filter
        if has_logo:
            overlay = Positions.overlay_pos(pos)
            if cfg.get("text"):
                # both logo and text
                text_f = self._text_filter(cfg, vinfo)
                # scale logo, overlay, then text
                # need to handle resolution scaling too
                scale_part = f",scale=-2:{target_h}" if target_h and target_h < H else ""
                fc = f"[0][1]scale2ref=w='iw*{logo_size}/100':h='ow/mdar'[wm][base];[base][wm]overlay={overlay}:format=yuv420p{scale_part}[tmp];[tmp]{text_f}[out]"
                cmd += ["-filter_complex", fc, "-map", "[out]"]
            else:
                # only logo
                scale_part = f",scale=-2:{target_h}" if target_h and target_h < H else ""
                fc = f"[0][1]scale2ref=w='iw*{logo_size}/100':h='ow/mdar':eval=frame[wm][base];[base][wm]overlay={overlay}:format=yuv420p{scale_part}"
                # opacity via format rgba + colorchannelmixer
                # For simplicity, we apply opacity via overlay format? Actually need extra filter for logo opacity
                # We'll use: [0]format=rgba,colorchannelmixer=aa={logo_op}[wm] then scale2ref
                fc = f"[0]format=rgba,colorchannelmixer=aa={logo_op}[l];[l][1]scale2ref=w='iw*{logo_size}/100':h='ow/mdar'[wm][base];[base][wm]overlay={overlay}:format=yuv420p{scale_part}"
                cmd += ["-filter_complex", fc]
        else:
            # only text
            text_f = self._text_filter(cfg, vinfo)
            if target_h and target_h < H:
                text_f = f"scale=-2:{target_h},{text_f}"
            cmd += ["-vf", text_f]

        # codec
        if codec == "h265":
            cmd += ["-c:v", "libx265", "-preset", preset, "-crf", str(crf)]
        else:
            if self.gpu == "nvenc":
                cmd += ["-c:v", "h264_nvenc", "-preset", "fast", "-cq", str(crf)]
            else:
                cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]

        # audio copy = speed
        if vinfo.get("has_audio"):
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-an"]

        # threads + faststart
        threads = psutil.cpu_count(logical=True) or 4
        cmd += ["-threads", str(threads), "-movflags", "+faststart", out]

        # print("CMD:", " ".join(cmd))
        try:
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            duration = vinfo.get("duration", 1) or 1
            while proc.returncode is None:
                await asyncio.sleep(0.5)
                try:
                    if os.path.exists(prog_file):
                        txt = Path(prog_file).read_text()
                        m = re.findall(r"out_time_ms=(\d+)", txt)
                        if m:
                            sec = int(m[-1])/1000000
                            p = min(sec/duration, 0.99)
                            if cb:
                                await cb(p, "🎬 Encoding")
                        if "progress=end" in txt:
                            break
                except:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.1)
                    break
                except asyncio.TimeoutError:
                    continue
            stdout, stderr = await proc.communicate()
            if os.path.exists(prog_file):
                os.remove(prog_file)
            if proc.returncode != 0:
                print(f"FFmpeg err: {stderr.decode()[:500]}")
                return False
            if cb:
                await cb(1.0, "✅ Done")
            return os.path.exists(out)
        except Exception as e:
            print(f"Video err: {e}")
            import traceback; traceback.print_exc()
            return False

    # ---------- IMAGE ----------
    async def image(self, inp: str, out: str, cfg: Dict) -> bool:
        try:
            img = Image.open(inp).convert("RGBA")
            W,H = img.size
            overlay = Image.new("RGBA", (W,H), (0,0,0,0))
            draw = ImageDraw.Draw(overlay)

            # font - try to support Hindi
            font_size = cfg.get("font_size", 36)
            font_size = max(12, int(min(W,H) * font_size / 720))
            font_path = find_font()
            try:
                font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
            except:
                font = ImageFont.load_default()

            # text can be multiline (from presets.png Hindi example)
            raw_text = cfg.get("text", "@MyChannel")
            lines = raw_text.split("\n")[:2]  # max 2 lines like presets.png

            # measure
            # calc max width
            max_w = 0
            total_h = 0
            line_sizes = []
            for line in lines:
                bbox = draw.textbbox((0,0), line, font=font)
                tw = bbox[2]-bbox[0]
                th = bbox[3]-bbox[1]
                line_sizes.append((tw,th))
                max_w = max(max_w, tw)
                total_h += th + 5
            total_h -= 5

            pos = cfg.get("position", "bottom_right")
            margin = int(min(W,H)*0.02)
            pos_map = {
                "top_left": (margin, margin),
                "top_right": (W-max_w-margin, margin),
                "bottom_left": (margin, H-total_h-margin),
                "bottom_right": (W-max_w-margin, H-total_h-margin),
                "center": ((W-max_w)//2, (H-total_h)//2)
            }
            x,y = pos_map.get(pos, pos_map["bottom_right"])

            # bg
            bg_enabled = cfg.get("bg_enabled", True)
            bg_color = cfg.get("bg_color", "black")
            bg_op = cfg.get("bg_opacity", 0.5)
            border = cfg.get("border", True)
            border_w = cfg.get("border_width", 3)

            if bg_color.startswith("#"):
                r,g,b = hex_to_rgb(bg_color)
            else:
                cmap = {"black":(0,0,0),"white":(255,255,255),"red":(200,0,0),"blue":(0,0,200),"green":(0,150,0),"yellow":(200,200,0),"orange":(255,100,0)}
                r,g,b = cmap.get(bg_color.lower(), (0,0,0))

            # bg box with padding + rounded corners
            pad = 12
            box = [x-pad, y-pad, x+max_w+pad, y+total_h+pad]

            if bg_enabled:
                # rounded rectangle for pill shape like presets.png
                try:
                    # Pill shape: radius = height/2
                    radius = int((total_h+pad*2)/2) if "Join us" in raw_text else 8
                    draw.rounded_rectangle(box, radius=radius, fill=(r,g,b,int(bg_op*255)))
                except:
                    draw.rectangle(box, fill=(r,g,b,int(bg_op*255)))

                if border:
                    try:
                        draw.rounded_rectangle(box, radius=radius, outline=(0,0,0,200), width=border_w)
                    except:
                        draw.rectangle(box, outline=(0,0,0,200), width=border_w)

            # shadow
            if cfg.get("shadow", True):
                sy = y+2
                for line, (tw,th) in zip(lines, line_sizes):
                    # align
                    lx = x + (max_w-tw)//2 if pos=="center" else x
                    draw.text((lx+2, sy+2), line, font=font, fill=(0,0,0,int(cfg.get("opacity",0.8)*255*0.5)))
                    sy += th+5

            # main text
            color = cfg.get("color", "white")
            opacity = cfg.get("opacity", 0.8)
            if color.startswith("#"):
                r,g,b = hex_to_rgb(color)
            else:
                cmap = {"white":(255,255,255),"black":(0,0,0),"red":(255,255,255),"yellow":(255,255,0),"cyan":(0,255,255),"magenta":(255,0,255),"green":(0,255,0),"blue":(100,150,255)}
                # for red bg, text white; for yellow bg black etc handled by user, but we keep white default
                r,g,b = cmap.get(color.lower(), (255,255,255))
                # special: if bg red and color white, keep white; if bg black and color white, white etc
                # Actually for presets.png: red box white text, black box white/yellow text
                if bg_color=="red" and color=="white":
                    r,g,b = (255,255,255)
                elif bg_color=="black" and color=="white":
                    r,g,b = (255,255,255)
                elif bg_color=="black" and color=="yellow":
                    r,g,b = (255,235,0)

            sy = y
            for line, (tw,th) in zip(lines, line_sizes):
                lx = x + (max_w-tw)//2 if pos=="center" else x
                draw.text((lx, sy), line, font=font, fill=(r,g,b,int(opacity*255)))
                sy += th+5

            # logo
            logo_path = cfg.get("logo_path")
            if logo_path and os.path.exists(logo_path):
                logo = Image.open(logo_path).convert("RGBA")
                size_pct = cfg.get("logo_size", 15)
                lw = int(W * size_pct / 100)
                lh = int(lw * logo.height / logo.width)
                logo = logo.resize((lw, lh), Image.LANCZOS)
                # opacity
                alpha = logo.split()[3]
                alpha = Image.eval(alpha, lambda a: int(a*cfg.get("logo_opacity",0.8)))
                logo.putalpha(alpha)
                # position logo separate from text to avoid overlap - use logo_position
                lpos = cfg.get("logo_position", "top_left")
                lx, ly = {
                    "top_left": (margin, margin),
                    "top_right": (W-lw-margin, margin),
                    "bottom_left": (margin, H-lh-margin),
                    "bottom_right": (W-lw-margin, H-lh-margin),
                    "center": ((W-lw)//2, (H-lh)//2)
                }.get(lpos, (margin, margin))
                # if text already at same pos, shift logo
                if lpos == pos:
                    # move logo to opposite
                    lpos = "top_left" if pos=="bottom_right" else "bottom_right"
                    lx, ly = {
                        "top_left": (margin, margin),
                        "bottom_right": (W-lw-margin, H-lh-margin)
                    }.get(lpos, (margin, margin))
                overlay.paste(logo, (lx, ly), logo)

            final = Image.alpha_composite(img, overlay)
            if out.lower().endswith((".jpg",".jpeg")):
                final = final.convert("RGB")
                final.save(out, "JPEG", quality=95, optimize=True)
            else:
                final.save(out, "PNG", optimize=True)
            return True
        except Exception as e:
            print(f"Image err: {e}")
            import traceback; traceback.print_exc()
            return False

    # ---------- PDF ----------
    async def pdf(self, inp: str, out: str, cfg: Dict, cb=None) -> bool:
        try:
            doc = fitz.open(inp)
            raw_text = cfg.get("text", "CONFIDENTIAL • @MyChannel")
            # first line only for PDF
            text = raw_text.split("\n")[0][:100]
            opacity = cfg.get("pdf_opacity", 0.15)
            fs = cfg.get("pdf_font_size", 32)
            rot = cfg.get("pdf_rotation", 45)
            color = cfg.get("pdf_color", "red")
            bg_op = cfg.get("pdf_bg_opacity", 0.1)
            mode = cfg.get("pdf_mode", "diagonal")

            if color.startswith("#"):
                r,g,b = hex_to_rgb(color)
                fcolor = (r/255, g/255, b/255)
            else:
                cmap = {"red":(1,0,0),"black":(0,0,0),"blue":(0,0,1),"green":(0,1,0),"gray":(0.5,0.5,0.5),"orange":(1,0.5,0)}
                fcolor = cmap.get(color.lower(), (1,0,0))

            total = len(doc)
            for i, page in enumerate(doc):
                if cb and i%2==0:
                    await cb(i/total, f"📄 PDF {i}/{total}")
                rect = page.rect
                W,H = rect.width, rect.height

                # handle rotated pages - fitz already handles, but we need to check
                # For landscape vs portrait, center still works

                if mode == "tiled":
                    # tiled diagonal across page - like pdf_check.png page2
                    x_step, y_step = 200, 150
                    # Use shape for opacity
                    shape = page.new_shape()
                    for x in range(0, int(W), x_step):
                        for y in range(0, int(H), y_step):
                            shape.insert_text(fitz.Point(x,y), text, fontsize=fs*0.5, rotate=rot, color=fcolor, overlay=True)
                    shape.finish(color=fcolor, fill_opacity=opacity)
                    shape.commit(overlay=True)

                elif mode == "diagonal":
                    # Single diagonal at bottom right + tiled light (like pdf_check.png)
                    # Bottom right solid
                    shape = page.new_shape()
                    # bottom right box
                    if bg_op>0:
                        # estimate box
                        tw = len(text)*fs*0.6
                        box = fitz.Rect(W-tw-40, H-30, W-10, H-10)
                        shape.draw_rect(box)
                        shape.finish(color=(0,0,0), fill=(0.9,0.9,0.9), fill_opacity=bg_op, width=0.5 if cfg.get("border") else 0)
                        shape.commit(overlay=True)
                        shape = page.new_shape()
                    # bottom right text
                    shape.insert_text(fitz.Point(W-len(text)*fs*0.35-15, H-15), text, fontsize=fs*0.4, color=fcolor)
                    shape.finish(fill_opacity=0.8)
                    shape.commit(overlay=True)

                    # tiled light across page
                    shape = page.new_shape()
                    for x in range(0, int(W), 250):
                        for y in range(0, int(H), 200):
                            shape.insert_text(fitz.Point(x,y), text, fontsize=fs*0.3, rotate=rot, color=(0.7,0.7,0.7))
                    shape.finish(fill_opacity=opacity*0.5)
                    shape.commit(overlay=True)

                else: # center
                    shape = page.new_shape()
                    shape.insert_text(fitz.Point(W/2-len(text)*fs*0.3, H/2), text, fontsize=fs, color=fcolor, rotate=rot)
                    shape.finish(fill_opacity=opacity)
                    shape.commit(overlay=True)

                # logo
                logo_path = cfg.get("logo_path")
                if logo_path and os.path.exists(logo_path):
                    try:
                        # top right or bottom
                        r = fitz.Rect(W-100, 20, W-20, 100)
                        page.insert_image(r, filename=logo_path, overlay=True, keep_proportion=True, alpha=int(opacity*255))
                    except:
                        pass

            doc.ez_save(out, garbage=4, deflate=True, clean=True)
            doc.close()
            if cb:
                await cb(1.0, "✅ PDF Done")
            return True
        except Exception as e:
            print(f"PDF err: {e}")
            import traceback; traceback.print_exc()
            return False

engine = Engine()
