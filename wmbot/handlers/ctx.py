"""Shared runtime state for handlers (filled in by main.py)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Awaiting:
    key: str                # settings key or admin action
    kind: str               # text | num | color | admin
    chat_id: int
    menu_msg_id: int
    prompt_msg_id: int
    back: str               # screen to re-render after success ("g:text", "n:font_size", …)
    menu_is_photo: bool = False


class Ctx:
    app: Any = None
    pipeline: Any = None
    transport: Any = None

    def __init__(self):
        self.pending: dict[str, Any] = {}                  # jobs waiting for user confirmation
        self.awaiting: dict[int, Awaiting] = {}            # user id → text prompt state
        self.menu_owner: dict[tuple[int, int], int] = {}   # (chat, msg) → user id
        self.last_dims: dict[int, tuple[int, int]] = {}    # user id → last media size (for previews)
        self.last_start_banner: dict[int, str] = {}        # avoid repeating the same random /start card
        self.started = time.time()
        self.me = None


ctx = Ctx()
