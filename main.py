#!/usr/bin/env python3
"""
Ultra Watermark Bot - Premium Balanced - Railway / Heroku / Render Ready
All features, systematic premium buttons, not messed up
Telegram upload only
"""
import asyncio
import os
from pyrogram import Client
from bot.config import Config
from bot.handlers import register
from rich.console import Console
from rich.panel import Panel

console = Console()

def banner():
    console.print(Panel(
        "[bold magenta]╔════════════════════════════════════════════════╗\n"
        "║  🎬 ULTRA WATERMARK BOT - PREMIUM v2.0          ║\n"
        "║  Perfect Balanced | All Features | High Speed  ║\n"
        "║  txt + bg opacity + border + CRF + size + all  ║\n"
        "║  Video + PDF + Image | Telegram Upload Only    ║\n"
        "║  Railway / Heroku / Render Ready               ║\n"
        "║  Premium Systematic Buttons - Not Messed Up    ║\n"
        "╚════════════════════════════════════════════════╝[/bold magenta]",
        border_style="cyan"
    ))

async def health_server():
    # Railway / Render health check - binds to PORT if exists
    port = int(os.getenv("PORT", "0"))
    if port == 0:
        return
    try:
        from aiohttp import web
        async def handle(request):
            return web.Response(text="🎬 Ultra Watermark Bot Premium is Running! ✅")
        app = web.Application()
        app.router.add_get("/", handle)
        app.router.add_get("/health", handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        console.print(f"[green]🌐 Health server running on port {port}[/green]")
        # Keep running
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        console.print(f"[yellow]Health server not started: {e}[/yellow]")

async def main():
    Config.init()
    banner()
    if not Config.API_ID or not Config.API_HASH or not Config.BOT_TOKEN:
        console.print("[red]❌ Missing API_ID/API_HASH/BOT_TOKEN in .env[/red]")
        console.print("For Railway: Add env vars in Railway dashboard")
        console.print("API_ID, API_HASH, BOT_TOKEN, WORKER_COUNT=3, MAX_QUEUE=50")
        return

    # Start health server in background for Railway/Render
    asyncio.create_task(health_server())

    app = Client(
        "ultra_premium_bot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        workers=32,
        max_concurrent_transmissions=16,
        sleep_threshold=30
    )

    register(app)

    console.print(f"[green]✅ Premium Config: Workers {Config.WORKER_COUNT} | Queue {Config.MAX_QUEUE} | Max {Config.MAX_FILE_SIZE_MB}MB | Preset {Config.DEFAULT_PRESET} | CRF {Config.DEFAULT_CRF}[/green]")
    console.print("[cyan]⚙️ Premium Systematic Buttons: Text, Mode, Position, Opacity, Color, Font, BG Box, Border, CRF, Preset, Resolution, Codec, PDF Mode[/cyan]")
    console.print("[cyan]🎬 Features: txt + bg opacity + border + crf + size + all from screenshots[/cyan]")
    console.print("[green]✅ Starting Premium Bot...[/green]")

    await app.start()
    console.print("[bold green]✅ Premium Bot Online! Send Video/PDF/Photo to watermark at ultra speed![/bold green]")
    console.print("[bold green]🚂 Railway Ready! Heroku Ready! Render Ready![/bold green]")
    await app.idle()
    await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[red]Stopped[/red]")
