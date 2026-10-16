import re
import os
import subprocess
from pathlib import Path

def fmt_bytes(b: int) -> str:
    for u in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_eta(s: float) -> str:
    if s <= 0 or s == float("inf"):
        return "0s"
    if s < 60:
        return f"{int(s)}s"
    if s < 3600:
        return f"{int(s//60)}m {int(s%60)}s"
    return f"{int(s//3600)}h {int((s%3600)//60)}m"

def sanitize(name: str) -> str:
    return re.sub(r'[<>:\"/\\|?*\x00-\x1f]', "_", name)[:150]

def escape_ffmpeg(txt: str) -> str:
    return txt.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%").replace("\n", " ")

def hex_to_rgb(h: str):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))

def detect_gpu():
    try:
        r = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=5)
        if "h264_nvenc" in r.stdout:
            return "nvenc"
        if "h264_videotoolbox" in r.stdout:
            return "videotoolbox"
    except:
        pass
    return "cpu"

def probe_video(path: str):
    import json
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        info = json.loads(r.stdout)
        vs = next((s for s in info.get("streams", []) if s.get("codec_type")=="video"), {})
        fmt = info.get("format", {})
        fps_s = vs.get("r_frame_rate", "30/1")
        try:
            n,d = fps_s.split("/")
            fps = float(n)/float(d) if float(d)!=0 else 30
        except:
            fps = 30
        return {
            "width": int(vs.get("width", 1280)),
            "height": int(vs.get("height", 720)),
            "duration": float(fmt.get("duration", 0)),
            "fps": fps,
            "has_audio": any(s.get("codec_type")=="audio" for s in info.get("streams", []))
        }
    except:
        return {"width":1280,"height":720,"duration":0,"fps":30,"has_audio":True}

def find_font():
    # Try to find best font supporting Hindi + English
    candidates = [
        "./assets/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None
