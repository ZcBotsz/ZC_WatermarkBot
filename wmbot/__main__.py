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
    ("start", "Welcome & quick start"), ("settings", "Design your watermark"), ("style", "One-tap watermark styles"),
    ("preview", "Preview your watermark"), ("setwm", "Set watermark text"), ("logo", "Upload / manage logo"),
    ("ddl", "Watermark a file from a direct link"), ("queue", "Your running jobs"), ("cancel", "Cancel jobs / input"),
    ("status", "Server status"), ("reset", "Reset settings"), ("help", "Help"),
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
    from .tgio import TgTransport

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    await db.start()
    app = Client(config.SESSION_NAME, api_id=config.API_ID, api_hash=config.API_HASH, bot_token=config.BOT_TOKEN,
                 workdir=str(config.DATA_DIR), workers=config.TG_WORKERS, sleep_threshold=60,
                 max_concurrent_transmissions=config.MAX_CONCURRENT_TRANSMISSIONS)
    transport = TgTransport(app)
    pipeline = Pipeline(transport)
    ctx.app, ctx.transport, ctx.pipeline = app, transport, pipeline
    register_all(app)

    await app.start()
    ctx.me = await app.get_me()
    try:
        await app.set_bot_commands([BotCommand(c, d) for c, d in COMMANDS])
    except Exception:
        log.warning("could not set the command list")
    await pipeline.start()

    from . import video
    hw = [e for c in ("h264", "h265") if (e := await video.hw_available(c))]
    log.info("✅ %s v%s online as @%s | cpu=%d encode_slots=%d gpu=%s | access=%s", config.BOT_NAME, __version__, ctx.me.username,
             config.CPU_COUNT, config.ENCODE_SLOTS, hw or "none", "public" if config.PUBLIC_MODE else "private")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:      # Windows
            pass
    await stop.wait()
    log.info("shutting down…")
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
