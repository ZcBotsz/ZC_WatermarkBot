"""Entry point:  python -m wmbot"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import __version__, config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("wmbot")

COMMANDS = [
    ("start", "Quick start"), ("settings", "Design your text watermark"),
    ("setwm", "Set watermark text"), ("preview", "Preview your watermark"),
    ("plans", "View plans"), ("myplan", "Show your plan"), ("usage", "Show your usage"),
    ("ddl", "Process a video or PDF from a link"), ("queue", "View your jobs"),
    ("automation", "Channel automation (Infinity plan)"),
    ("cancel", "Cancel jobs or input"), ("status", "Server status"),
    ("reset", "Reset settings"), ("help", "Help"), ("id", "Show your numeric Telegram ID"),
]


async def amain() -> None:
    problems = config.validate()
    if problems:
        print("\n❌ Configuration problems:\n  - " + "\n  - ".join(problems) + "\n\nCopy .env.example to .env and fill it in.")
        sys.exit(1)

    from pyrogram import Client
    from pyrogram.types import BotCommand

    from .db import db
    from .handlers import register_all
    from .handlers.ctx import ctx
    from .jobs import Pipeline
    from .channel_automation import ChannelAutomation
    from .tgio import TgTransport, small_caps_text

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await db.start()
    app = Client(config.SESSION_NAME, api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN,
                 workdir=str(config.DATA_DIR), workers=config.TG_WORKERS, sleep_threshold=60,
                 max_concurrent_transmissions=config.MAX_CONCURRENT_TRANSMISSIONS)
    transport = TgTransport(app)
    pipeline = Pipeline(transport)
    automation = ChannelAutomation(app, pipeline)
    ctx.app, ctx.transport, ctx.pipeline, ctx.automation = app, transport, pipeline, automation
    register_all(app)

    await app.start()
    ctx.me = await app.get_me()
    transport.bot_username = ctx.me.username
    try:
        await app.set_bot_commands([BotCommand(c, small_caps_text(d)) for c, d in COMMANDS])
    except Exception:
        log.warning("could not set the command list")
    await pipeline.start()
    await automation.start()

    from . import video
    from .fonts import refresh_font_choices
    hw = [e for c in ("h264", "h265") if (e := await video.hw_available(c))]
    font_count = len(refresh_font_choices())
    ram_cap = f"{config.MEMORY_LIMIT_MB}MB" if config.MEMORY_LIMIT_MB else "unknown"
    log.info("✅ %s v%s online as @%s | cpu=%d encoders=%d ffmpeg_threads=%d ram_cap=%s gpu=%s fonts=%d | access=%s",
             config.BOT_NAME, __version__, ctx.me.username, config.CPU_COUNT, config.ENCODE_SLOTS,
             config.FFMPEG_THREADS, ram_cap, hw or "none", font_count, "public" if config.PUBLIC_MODE else "private")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:      # Windows
            pass
    await stop.wait()
    log.info("shutting down…")
    await automation.stop()
    await pipeline.stop()
    await app.stop()
    await db.close()


def main() -> None:
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
