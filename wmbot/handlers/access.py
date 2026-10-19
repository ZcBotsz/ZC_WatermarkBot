from __future__ import annotations

from pyrogram.enums import ChatType

from .. import config
from ..db import db

GROUPS = (ChatType.GROUP, ChatType.SUPERGROUP)


def is_admin(uid: int) -> bool:
    # OWNER_ID is the single authoritative admin identity used on Railway.
    return bool(config.OWNER_ID and uid == config.OWNER_ID)


def allowed(user, chat) -> bool:
    if user is None or chat is None:
        return False
    uid = user.id
    if db.is_banned(uid):
        return False
    if is_admin(uid):
        return True
    if chat.type == ChatType.PRIVATE:
        return config.PUBLIC_MODE or db.is_allowed_user(uid)
    if chat.type in GROUPS:
        return config.PUBLIC_MODE or db.is_auth_chat(chat.id) or db.is_allowed_user(uid)
    return False


def deny_text(uid: int) -> str:
    return (f"🔒 <b>This bot is private.</b>\nAsk the owner to allow your ID: <code>{uid}</code>")
