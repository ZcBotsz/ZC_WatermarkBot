"""Hard-coded /start loading animation (the bar style supplied by the owner).

Telegram cannot send a truly animated *text* message, so this does what the
reference implementation does: send one message, edit it through the bar
frames while showing a "typing…" chat action, then hide it so only the real
/start menu remains. There is deliberately **no setting** for this — it plays
on every /start, exactly as defined here.
"""
from __future__ import annotations

import asyncio
import inspect

from pyrogram.enums import ChatAction, ParseMode

from .tgio import safe_edit, send_message

H = ParseMode.HTML

# ▱▱▱▱▱▱▱▱ → ▰▰▰▰▰▰▰▱ → ▰▰▰▰▰▰▰▰  (8 cells, supplied design)
BAR_FRAMES: tuple[str, ...] = tuple(
    "Loading…\n" + "▰" * filled + "▱" * (8 - filled) for filled in range(9)
)
FRAME_SECONDS = 0.25   # 8 edits ≈ 2 seconds total


async def _maybe_await(result):
    if inspect.isawaitable(result):
        return await result
    return result


async def play_start_animation(client, chat_id: int):
    """Play the bar animation in `chat_id`. Returns the animation message or None."""
    try:
        await _maybe_await(client.send_chat_action(chat_id, ChatAction.TYPING))
    except Exception:
        pass
    try:
        message = await send_message(client, chat_id, BAR_FRAMES[0], parse_mode=H)
    except Exception:
        return None
    if message is None:
        return None
    for frame in BAR_FRAMES[1:]:
        await asyncio.sleep(FRAME_SECONDS)
        try:
            await safe_edit(client.edit_message_text, chat_id, message.id, frame, parse_mode=H)
        except Exception:
            pass  # rate-limited / not-modified edits must not break /start
    return message


async def hide_start_animation(message) -> None:
    """Remove the animation message so only the final /start menu stays visible."""
    if message is None:
        return
    try:
        await _maybe_await(message.delete())
    except Exception:
        pass
