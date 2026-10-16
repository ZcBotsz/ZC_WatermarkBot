import os
import time
import asyncio
import uuid
import shutil
from pathlib import Path

from pyrogram import filters
from pyrogram.types import Message, CallbackQuery

from .config import Config, Modes, Quality
from .database import db
from .watermark_engine import engine
from .keyboards import *
from .utils import fmt_bytes, sanitize
from .progress import Tracker

job_queue = asyncio.Queue(maxsize=Config.MAX_QUEUE)
active_jobs = {}
user_cooldown = {}

HELP = """
🎬 **ULTRA WATERMARK BOT - PREMIUM v2.0**

**✨ Premium Features:**
🎥 Video: Text + Logo, 8 modes, bg box opacity, border, CRF, preset, res, codec
📄 PDF: Diagonal/Tiled/Center, opacity, bg, border, logo, landscape/rotated
🖼️ Image: Rounded pill bg, border, shadow, logo, Hindi Unicode नमस्ते दोस्तों

**⚙️ Systematic Settings:**
📝 Text | 🎭 Mode | 📍 Position | 👁️ Opacity | 🎨 Color | 🔤 Font | 🟦 BG Box | ⬛ Border | ⚡ CRF | 🏃 Preset | 📐 Res | 🎞️ Codec | 📄 PDF Mode

**🚀 Commands:**
/start - Premium menu
/settings - Systematic settings
/config - Show config
/reset - Reset all
/queue - Queue status
/watermark <text> - Set text (Hindi supported)
/mode <mode> - Set mode
/font <size> - Font 10-200
/bgop <0.0-1.0> - BG opacity
/op <0.0-1.0> - Text opacity
/crf <18-30> - Quality
/setlogo - Set logo
/dellogo - Remove logo

**🎭 Modes:** static 📌, bouncing 🏓 (best anti-removal), floating 🌊, scrolling 📰, diagonal ↗️, tiled 🔲, transparent 👻, cinematic 🎞️
**📍 Pos:** ↖️ top_left, ↗️ top_right, ↙️ bottom_left, ↘️ bottom_right, 🎯 center
**⚡ Preset:** ultrafast ⚡ 14s to veryslow 🐢 3m20s
**📐 Res:** original,1080p,720p,540p,480p,360p
**🎞️ Codec:** h264 fast, h265 small 50%

**📤 Send Video/PDF/Photo to watermark at ultra speed!**
**🚂 Railway / Heroku / Render Ready**
"""

SPEED = """
🚀 **PREMIUM SPEED - How it works:**

1. `-c:a copy` = no audio re-encode = 50% faster (Divkix)
2. `ultrafast` preset = fastest 14s vs 3m20s veryslow
3. `threads=cpu_count` = all cores
4. `scale2ref` logo 15% width fast
5. GPU `h264_nvenc` auto if NVIDIA
6. Progress file + throttle 2s = no FloodWait
7. Worker pool 3 workers = parallel
8. PyMuPDF C-based PDF 1.2s for 10 pages
9. PIL LANCZOS + alpha_composite image 0.3s
10. workers=32, max_trans=16 = fast TG upload

**Benchmark bench/:** 1080p 27MB ultrafast bouncing = 14s

Use preset ultrafast + CRF 23 + H264 for max speed!
"""

def register(app):

    @app.on_message(filters.command("start") & filters.private)
    async def start_c(client, message: Message):
        cfg = await db.get(message.from_user.id)
        await message.reply_text(
            f"🎬 **ULTRA WATERMARK BOT - PREMIUM v2.0**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👋 Welcome {message.from_user.first_name}!\n\n"
            f"**📝 Text:** `{cfg['text'][:50]}`\n"
            f"**🎭 Mode:** `{cfg['mode']}` | **📍 Pos:** `{cfg['position']}`\n"
            f"**🏃 Preset:** `{cfg['preset']}` | **⚡ CRF:** `{cfg['crf']}` | **📐 Res:** `{cfg['resolution']}`\n"
            f"**🎨 Color:** `{cfg['color']}` | **👁️ Op:** `{cfg['opacity']}`\n"
            f"**🟦 BG:** `{cfg['bg_color']}@{cfg['bg_opacity']}` | **⬛ Border:** `{cfg['border']}` W:{cfg['border_width']}\n"
            f"**🖼️ Logo:** `{'✅ Set' if cfg['logo_path'] else '❌ None'}` {cfg['logo_size']}%\n\n"
            f"**📤 Send Video / PDF / Photo to watermark at ultra speed!**\n"
            f"**⚙️ Use Settings for premium systematic controls**\n\n"
            f"🚂 **Railway / Heroku / Render Ready**",
            reply_markup=main_menu()
        )

    @app.on_message(filters.command(["help","settings","config","queue","reset"]) & filters.private)
    async def cmds(client, message: Message):
        txt = message.text.lower()
        uid = message.from_user.id
        if "help" in txt:
            await message.reply_text(HELP, reply_markup=main_menu())
        elif "settings" in txt:
            await message.reply_text("⚙️ **Premium Systematic Settings**\nGrouped by category - not messed up:", reply_markup=settings_menu())
        elif "config" in txt:
            cfg = await db.get(uid)
            t = (
                f"⚙️ **Premium Config - Systematic**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"**📝 Text:** `{cfg['text'][:80]}`\n"
                f"**🎭 Mode:** `{cfg['mode']}` - {Modes.DESC.get(cfg['mode'],'')}\n"
                f"**📍 Position:** `{cfg['position']}` | **Logo Pos:** `{cfg['logo_position']}`\n"
                f"**👁️ Opacity:** `{cfg['opacity']}` | **Logo Op:** `{cfg['logo_opacity']}`\n"
                f"**🎨 Color:** `{cfg['color']}` | **🔤 Font:** `{cfg['font_size']}`\n"
                f"**🟦 BG:** `{cfg['bg_color']}@{cfg['bg_opacity']}` Enabled: `{cfg['bg_enabled']}`\n"
                f"**⬛ Border:** `{cfg['border']}` W: `{cfg['border_width']}` Color: `{cfg['border_color']}`\n"
                f"**⚡ CRF:** `{cfg['crf']}` - {Quality.CRF_DESC.get(cfg['crf'],'')} | **🏃 Preset:** `{cfg['preset']}`\n"
                f"**📐 Res:** `{cfg['resolution']}` | **🎞️ Codec:** `{cfg['codec']}`\n"
                f"**📄 PDF:** `{cfg['pdf_mode']}` Op: `{cfg['pdf_opacity']}` Rot: `{cfg['pdf_rotation']}°` BG Op: `{cfg['pdf_bg_opacity']}`\n"
                f"**🖼️ Logo:** `{'✅' if cfg['logo_path'] else '❌'}` Size: `{cfg['logo_size']}%` Path: `{cfg['logo_path'] or 'None'}`"
            )
            await message.reply_text(t, reply_markup=settings_menu())
        elif "queue" in txt:
            await message.reply_text(f"📋 **Queue Status - Premium**\n━━━━━━━━━━━━\n📬 Waiting: `{job_queue.qsize()}`\n⚡ Active: `{len(active_jobs)}`\n👷 Workers: `{Config.WORKER_COUNT}`\n📦 Max Queue: `{Config.MAX_QUEUE}`")
        elif "reset" in txt:
            await db.reset(uid)
            await message.reply_text("✅ **Reset to Premium Defaults!**", reply_markup=main_menu())

    @app.on_message(filters.command(["watermark","mode","font","bgop","op","crf","setlogo","dellogo","bgcolor","borderw"]) & filters.private)
    async def quick_set(client, message: Message):
        uid = message.from_user.id
        cmd = message.command[0].lower()
        if len(message.command) < 2 and cmd not in ["setlogo","dellogo"]:
            await message.reply_text(f"Usage: /{cmd} <value>\nExample: /{cmd} value")
            return
        if cmd == "watermark":
            txt = message.text.split(None,1)[1]
            await db.update(uid, text=txt)
            await message.reply_text(f"✅ **Text Set:** `{txt[:100]}`\nSupports Hindi: नमस्ते दोस्तों", reply_markup=main_menu())
        elif cmd == "mode":
            m = message.command[1].lower()
            if m in Modes.ALL:
                await db.update(uid, mode=m)
                await message.reply_text(f"✅ **Mode:** `{m}` - {Modes.DESC.get(m,'')}")
            else:
                await message.reply_text(f"Modes: {', '.join(Modes.ALL)}")
        elif cmd == "font":
            try:
                sz = int(message.command[1])
                if 10 <= sz <= 200:
                    await db.update(uid, font_size=sz, pdf_font_size=sz)
                    await message.reply_text(f"✅ **Font:** `{sz}`")
            except:
                await message.reply_text("Font 10-200")
        elif cmd == "bgop":
            try:
                v = float(message.command[1])
                if 0 <= v <= 1:
                    await db.update(uid, bg_opacity=v, pdf_bg_opacity=v)
                    await message.reply_text(f"✅ **BG Opacity:** `{v}`")
            except:
                await message.reply_text("BG Op 0.0-1.0")
        elif cmd == "op":
            try:
                v = float(message.command[1])
                if 0 <= v <= 1:
                    await db.update(uid, opacity=v, logo_opacity=v, pdf_opacity=v)
                    await message.reply_text(f"✅ **Opacity:** `{v}`")
            except:
                await message.reply_text("Op 0.0-1.0")
        elif cmd == "crf":
            try:
                v = int(message.command[1])
                if 0 <= v <= 51:
                    await db.update(uid, crf=v)
                    await message.reply_text(f"✅ **CRF:** `{v}` - {Quality.CRF_DESC.get(v,'')}")
            except:
                await message.reply_text("CRF 0-51")
        elif cmd == "bgcolor":
            c = message.command[1].lower()
            await db.update(uid, bg_color=c)
            await message.reply_text(f"✅ **BG Color:** `{c}`")
        elif cmd == "borderw":
            try:
                w = int(message.command[1])
                await db.update(uid, border_width=w)
                await message.reply_text(f"✅ **Border Width:** `{w}`")
            except:
                await message.reply_text("Border width 1-5")
        elif cmd == "setlogo":
            await message.reply_text("📤 **Premium Logo Setup**\nSend image/photo with caption `logo`\nOr reply to this message with image.\nLogo will be 15% width, rounded handling.")
        elif cmd == "dellogo":
            cfg = await db.get(uid)
            if cfg.get("logo_path") and os.path.exists(cfg["logo_path"]):
                os.remove(cfg["logo_path"])
            await db.update(uid, logo_path=None)
            await message.reply_text("✅ **Logo Removed!**")

    @app.on_callback_query()
    async def cbs(client, query: CallbackQuery):
        data = query.data
        uid = query.from_user.id

        if data == "menu_main":
            await query.message.edit_text("🎬 **ULTRA WATERMARK BOT - PREMIUM**\n━━━━━━━━━━━━\nChoose premium action:", reply_markup=main_menu())
        elif data == "menu_settings":
            await query.message.edit_text("⚙️ **Premium Systematic Settings**\n━━━━━━━━━━━━\nGrouped by category, not messed up:", reply_markup=settings_menu())
        elif data == "menu_help":
            await query.message.edit_text(HELP, reply_markup=main_menu())
        elif data == "menu_speed":
            await query.message.edit_text(SPEED, reply_markup=main_menu())
        elif data == "menu_config":
            cfg = await db.get(uid)
            await query.message.edit_text(f"📊 **Config:**\n📝 `{cfg['text'][:60]}`\n🎭 {cfg['mode']} | 📍 {cfg['position']}\n⚡ {cfg['preset']} CRF {cfg['crf']} | 📐 {cfg['resolution']}\n🟦 BG {cfg['bg_color']}@{cfg['bg_opacity']} | ⬛ Border {cfg['border']}", reply_markup=settings_menu())
        elif data in ["menu_video","menu_pdf","menu_image","menu_logo"]:
            mp = {"menu_video":"🎬 **Video Watermark - Premium**\nSend any video now!\nSupports MP4,MKV,MOV,AVI,WEBM\n8 modes, bg box, border, CRF, preset, res, codec, logo","menu_pdf":"📄 **PDF Watermark - Premium**\nSend any PDF now!\nDiagonal/Tiled/Center, opacity, bg, border, logo\nHandles portrait/landscape/rotated","menu_image":"🖼️ **Image Watermark - Premium**\nSend any photo now!\nRounded pill bg, border, shadow, logo, Hindi","menu_logo":"🎨 **Premium Logo Setup**\nSend image with caption `logo`\n15% width, LANCZOS, opacity, separate pos"}
            await query.message.edit_text(mp[data], reply_markup=main_menu())

        elif data.startswith("mode_"):
            m = data.replace("mode_","",1)
            await db.update(uid, mode=m)
            await query.answer(f"✅ Mode {m}")
            await query.message.edit_text(f"✅ **Mode:** `{m}`\n{Modes.DESC.get(m,'')}\n\nFrom screenshots: bouncing = DVD best anti-removal", reply_markup=settings_menu())
        elif data.startswith("pos_"):
            p = data.replace("pos_","",1)
            await db.update(uid, position=p)
            await query.answer(f"✅ Pos {p}")
            await query.message.edit_text(f"✅ **Position:** `{p}`\n↖️ Top Left, ↗️ Top Right, ↙️ Bottom Left, ↘️ Bottom Right, 🎯 Center", reply_markup=settings_menu())
        elif data.startswith("preset_"):
            p = data.replace("preset_","",1)
            await db.update(uid, preset=p)
            await query.answer(f"✅ Preset {p}")
            await query.message.edit_text(f"✅ **Preset:** `{p}`\n{Quality.PRESET_DESC.get(p,'')}\n⚡ ultrafast=14s fastest, veryslow=3m20s smallest", reply_markup=settings_menu())
        elif data.startswith("res_"):
            r = data.replace("res_","",1)
            await db.update(uid, resolution=r)
            await query.answer(f"✅ Res {r}")
            await query.message.edit_text(f"✅ **Resolution:** `{r}`\nOriginal keeps source, 1080p-360p scales", reply_markup=settings_menu())
        elif data.startswith("color_"):
            c = data.replace("color_","",1)
            await db.update(uid, color=c, pdf_color=c)
            await query.answer(f"✅ Color {c}")
            await query.message.edit_text(f"✅ **Color:** `{c}`\nFor image: white on red/black boxes like presets.png", reply_markup=settings_menu())
        elif data.startswith("pdfmode_"):
            m = data.replace("pdfmode_","",1)
            await db.update(uid, pdf_mode=m)
            await query.answer(f"✅ PDF {m}")
            await query.message.edit_text(f"✅ **PDF Mode:** `{m}`\nDiagonal = bottom right + tiled light (pdf_check.png)\nTiled = 200x150 grid", reply_markup=settings_menu())
        elif data.startswith("crf_"):
            v = int(data.replace("crf_","",1))
            await db.update(uid, crf=v)
            await query.answer(f"✅ CRF {v}")
            await query.message.edit_text(f"✅ **CRF:** `{v}`\n{Quality.CRF_DESC.get(v,'')}\n18 high quality big, 23 default, 28 small", reply_markup=settings_menu())
        elif data.startswith("op_"):
            v = float(data.replace("op_","",1))
            await db.update(uid, opacity=v, logo_opacity=v)
            await query.answer(f"✅ Op {v}")
            await query.message.edit_text(f"✅ **Opacity:** `{v*100:.0f}%`\n20% ghost 👻, 60% default, 100% solid", reply_markup=settings_menu())
        elif data.startswith("codec_"):
            c = data.replace("codec_","",1)
            await db.update(uid, codec=c)
            await query.answer(f"✅ Codec {c}")
            await query.message.edit_text(f"✅ **Codec:** `{c}`\nH264 fast compatible, H265 small 50% but slower", reply_markup=settings_menu())
        elif data.startswith("bgop_"):
            v = float(data.replace("bgop_","",1))
            await db.update(uid, bg_opacity=v, pdf_bg_opacity=v)
            await query.answer(f"✅ BG Op {v}")
            await query.message.edit_text(f"✅ **BG Opacity:** `{v}`\n0.0 transparent, 0.5 default, 1.0 solid", reply_markup=settings_menu())
        elif data.startswith("bgcolor_"):
            c = data.replace("bgcolor_","",1)
            await db.update(uid, bg_color=c)
            await query.answer(f"✅ BG Color {c}")
            await query.message.edit_text(f"✅ **BG Color:** `{c}`\nBlack pill like presets.png top, Red box bottom", reply_markup=settings_menu())
        elif data.startswith("borderw_"):
            w = int(data.replace("borderw_","",1))
            await db.update(uid, border_width=w, border=True)
            await query.answer(f"✅ Border W {w}")
            await query.message.edit_text(f"✅ **Border Width:** `{w}`\nRounded pill for image", reply_markup=settings_menu())

        elif data == "set_mode":
            await query.message.edit_text("🎭 **Choose Mode - Premium**\n8 modes from screenshots:", reply_markup=mode_kb())
        elif data == "set_position":
            await query.message.edit_text("📍 **Choose Position - Systematic:**", reply_markup=pos_kb())
        elif data == "set_preset":
            await query.message.edit_text("🏃 **Choose Preset Speed - Fastest to Slowest:**", reply_markup=preset_kb())
        elif data == "set_res":
            await query.message.edit_text("📐 **Choose Resolution - Size:**", reply_markup=res_kb())
        elif data == "set_color":
            await query.message.edit_text("🎨 **Choose Color - 9 colors from CBML:**", reply_markup=color_kb())
        elif data == "set_pdfmode":
            await query.message.edit_text("📄 **Choose PDF Mode - From pdf_check.png:**", reply_markup=pdf_kb())
        elif data == "set_crf":
            await query.message.edit_text("⚡ **Choose CRF Quality - 18 High to 30 Tiny:**", reply_markup=crf_kb())
        elif data == "set_opacity":
            await query.message.edit_text("👁️ **Choose Opacity - Ghost to Solid:**", reply_markup=opacity_kb())
        elif data == "set_codec":
            await query.message.edit_text("🎞️ **Choose Codec - Fast vs Small:**", reply_markup=codec_kb())
        elif data == "set_bg":
            await query.message.edit_text("🟦 **BG Box - Background Opacity + Color - Your Request:**\nBG opacity 0.0-1.0 + color black/white/red", reply_markup=bg_kb())
        elif data == "set_border":
            await query.message.edit_text("⬛ **Border - Your Request:**\nBorder ON/OFF + width 1-5 + rounded pill", reply_markup=border_kb())
        elif data == "set_font":
            await query.message.edit_text("🔤 **Font Size:**\nSend `/font 48` Range 10-200\nScaled for image, direct for video/PDF\nSupports Hindi Unicode")
        elif data == "set_text":
            await query.message.edit_text("📝 **Text Watermark - Premium:**\nSend `/watermark Your Text`\nSupports:\n- Hindi: `नमस्ते दोस्तों`\n- Multiline: Use \\n for 2 lines image\n- Example: `@MyChannel • Join us`")
        elif data == "set_reset":
            await db.reset(uid)
            await query.message.edit_text("✅ **Reset to Premium Defaults!**\nText: @MyChannel • Join us\nMode: bouncing | Pos: bottom_right\nPreset: ultrafast | CRF 23", reply_markup=main_menu())
        elif data == "bg_on":
            await db.update(uid, bg_enabled=True)
            await query.message.edit_text("✅ **BG Box ON** - Background box enabled", reply_markup=settings_menu())
        elif data == "bg_off":
            await db.update(uid, bg_enabled=False)
            await query.message.edit_text("✅ **BG Box OFF** - No background", reply_markup=settings_menu())
        elif data == "border_on":
            await db.update(uid, border=True)
            await query.message.edit_text("✅ **Border ON**", reply_markup=settings_menu())
        elif data == "border_off":
            await db.update(uid, border=False)
            await query.message.edit_text("✅ **Border OFF**", reply_markup=settings_menu())
        else:
            await query.answer("Unknown")

    @app.on_message(filters.photo & filters.private)
    async def photo_h(client, message: Message):
        cap = (message.caption or "").lower()
        if "logo" in cap or (message.reply_to_message and "logo" in (message.reply_to_message.text or "").lower()):
            uid = message.from_user.id
            udir = Path(f"{Config.TEMP_DIR}/users/{uid}")
            udir.mkdir(parents=True, exist_ok=True)
            logo_p = udir / "logo.png"
            await client.download_media(message, file_name=str(logo_p))
            await db.update(uid, logo_path=str(logo_p))
            await message.reply_text(f"✅ **Premium Logo Saved!** {fmt_bytes(logo_p.stat().st_size)}\nSize 15% width, LANCZOS, opacity { (await db.get(uid))['logo_opacity']}\nNow send video/pdf/image.", reply_markup=main_menu())
            return
        await handle_media(client, message, "photo")

    @app.on_message(filters.document & filters.private)
    async def doc_h(client, message: Message):
        mime = message.document.mime_type or ""
        name = message.document.file_name or ""
        if "pdf" in mime or name.lower().endswith(".pdf"):
            await handle_media(client, message, "pdf")
        elif mime.startswith("video/") or name.lower().endswith((".mp4",".mkv",".mov",".avi",".webm",".m4v")):
            await handle_media(client, message, "video")
        elif mime.startswith("image/"):
            await handle_media(client, message, "photo")
        else:
            await handle_media(client, message, "video")

    @app.on_message(filters.video & filters.private)
    async def video_h(client, message: Message):
        await handle_media(client, message, "video")

    async def handle_media(client, message: Message, mtype: str):
        uid = message.from_user.id
        now = time.time()
        if uid in user_cooldown and now - user_cooldown[uid] < 3:
            await message.reply_text("⏳ **Premium Anti-Spam:** Wait 3 sec...")
            return
        user_cooldown[uid] = now

        fsize = 0
        fname = "file"
        if message.document:
            fsize = message.document.file_size or 0
            fname = message.document.file_name or f"doc_{int(now)}"
        elif message.video:
            fsize = message.video.file_size or 0
            fname = message.video.file_name or f"video_{int(now)}.mp4"
        elif message.photo:
            fname = f"photo_{int(now)}.jpg"

        max_b = Config.MAX_FILE_SIZE_MB*1024*1024
        if fsize and fsize > max_b:
            await message.reply_text(f"❌ **Too large:** {fmt_bytes(fsize)} > {fmt_bytes(max_b)}")
            return

        cfg = await db.get(uid)
        job_id = str(uuid.uuid4())[:8]
        prog_msg = await message.reply_text(f"🎬 **ULTRA PREMIUM**\n━━━━━━━━━━━━\n📄 `{fname[:40]}`\n📦 {fmt_bytes(fsize)} | 🎭 {cfg['mode']}\n⏳ Queuing job `{job_id}`")

        job = {
            "job_id": job_id,
            "user_id": uid,
            "chat_id": message.chat.id,
            "msg_id": message.id,
            "prog_id": prog_msg.id,
            "fname": fname,
            "fsize": fsize,
            "mtype": mtype,
            "cfg": cfg,
            "ts": now
        }
        try:
            job_queue.put_nowait(job)
            await prog_msg.edit_text(f"🎬 **ULTRA PREMIUM**\n━━━━━━━━━━━━\n📄 `{fname[:40]}`\n📦 {fmt_bytes(fsize)} | 🎭 {cfg['mode']} | 📍 {cfg['position']}\n📋 Queue: `{job_queue.qsize()}` | ⚡ {cfg['preset']} CRF {cfg['crf']}\n⏳ Waiting for premium worker...")
        except asyncio.QueueFull:
            await prog_msg.edit_text("❌ **Queue Full!** Premium workers busy, try 1 min later.")

    async def worker(wid: int):
        print(f"[Worker {wid}] Premium started")
        while True:
            try:
                job = await job_queue.get()
                active_jobs[job["job_id"]] = job
                await process_job(app, job)
                job_queue.task_done()
                active_jobs.pop(job["job_id"], None)
            except Exception as e:
                print(f"[Worker {wid}] err: {e}")
                import traceback; traceback.print_exc()
                await asyncio.sleep(1)

    async def process_job(client, job):
        jid = job["job_id"]
        chat_id = job["chat_id"]
        msg_id = job["msg_id"]
        prog_id = job["prog_id"]
        fname = job["fname"]
        mtype = job["mtype"]
        cfg = job["cfg"]

        jdir = Path(f"{Config.TEMP_DIR}/jobs/{jid}")
        jdir.mkdir(parents=True, exist_ok=True)
        tracker = Tracker(client, chat_id, prog_id, interval=2.0)

        try:
            await tracker.update(0.05, "⬇️ Downloading Premium")
            orig = await client.get_messages(chat_id, msg_id)
            inp = str(jdir / sanitize(fname))
            start = time.time()

            async def dl_prog(cur, tot):
                elapsed = time.time()-start
                speed = cur / max(elapsed,0.1)
                eta = (tot-cur)/max(speed,1) if tot else 0
                pct = cur/tot if tot else 0
                await tracker.update(pct*0.4, "⬇️ Downloading", f"{fmt_bytes(int(speed))}/s", f"{int(eta)}s")

            await client.download_media(orig, file_name=inp, progress=dl_prog)
            if not os.path.exists(inp):
                await tracker.edit("❌ Download failed")
                return

            out = ""
            ok = False
            if mtype == "video":
                out = str(jdir / f"wm_{Path(fname).stem}.mp4")
                await tracker.update(0.45, "🎬 Watermarking Video Premium")
                async def enc_cb(p, stage):
                    await tracker.update(0.45+p*0.45, stage)
                ok = await engine.video(inp, out, cfg, cb=enc_cb)
            elif mtype == "pdf":
                out = str(jdir / f"wm_{Path(fname).stem}.pdf")
                await tracker.update(0.45, "📄 Watermarking PDF Premium")
                async def pdf_cb(p, stage):
                    await tracker.update(0.45+p*0.45, stage)
                ok = await engine.pdf(inp, out, cfg, cb=pdf_cb)
            else:
                ext = Path(fname).suffix or ".jpg"
                out = str(jdir / f"wm_{Path(fname).stem}{ext}")
                await tracker.update(0.5, "🖼️ Watermarking Image Premium")
                ok = await engine.image(inp, out, cfg)
                await tracker.update(0.9, "🖼️ Done Premium")

            if not ok or not os.path.exists(out):
                await tracker.edit("❌ **Premium Watermark Failed** - Check logs")
                return

            await tracker.update(0.92, "⬆️ Uploading to Telegram Premium")
            start = time.time()
            async def ul_prog(cur, tot):
                elapsed = time.time()-start
                speed = cur / max(elapsed,0.1)
                eta = (tot-cur)/max(speed,1) if tot else 0
                pct = cur/tot if tot else 0
                await tracker.update(0.92+pct*0.08, "⬆️ Uploading", f"{fmt_bytes(int(speed))}/s", f"{int(eta)}s")

            osize = os.path.getsize(out)
            cap = (
                f"✅ **Premium Watermarked!**\n"
                f"━━━━━━━━━━━━\n"
                f"📄 `{Path(out).name}`\n"
                f"📦 {fmt_bytes(osize)} | 🎭 {cfg['mode']} | 📍 {cfg['position']}\n"
                f"⚡ {cfg['preset']} CRF {cfg['crf']} | 📐 {cfg['resolution']} {cfg['codec']}\n"
                f"🎨 {cfg['color']} Op {cfg['opacity']} | 🟦 BG {cfg['bg_color']}@{cfg['bg_opacity']} | ⬛ Border {cfg['border']} W{cfg['border_width']}\n"
                f"🎬 **Ultra Premium Bot** | 🚂 Railway Ready"
            )

            if mtype == "video":
                await client.send_video(chat_id, out, caption=cap, progress=ul_prog)
            elif mtype == "pdf":
                await client.send_document(chat_id, out, caption=cap, progress=ul_prog)
            else:
                await client.send_photo(chat_id, out, caption=cap, progress=ul_prog)

            await tracker.edit(f"✅ **Premium Done!**\n━━━━━━━━━━━━\n📄 `{fname[:30]}` → `{Path(out).name}`\n📦 {fmt_bytes(osize)} | ⏱️ {int(time.time()-job['ts'])}s\n🎭 {cfg['mode']} | {cfg['preset']} CRF {cfg['crf']}\n🚂 Railway/Render/Heroku Ready")

        except Exception as e:
            import traceback
            print(f"Job {jid} fail: {e}\n{traceback.format_exc()}")
            try:
                await client.edit_message_text(chat_id, prog_id, f"❌ **Failed:** `{str(e)[:200]}`")
            except:
                pass
        finally:
            try:
                if jdir.exists():
                    shutil.rmtree(jdir, ignore_errors=True)
            except:
                pass

    for i in range(Config.WORKER_COUNT):
        asyncio.create_task(worker(i))
