"""keyboards, program files, mass sending and the timed notices (open, reminder, close)"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaDocument
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import texts
from .config import Election, Phase, Settings, now_utc
from .db import Storage, User

log = logging.getLogger(__name__)

# telegram allows about 30 messages per second in total, stay well below
SEND_INTERVAL = 0.05


def ballot_kb(e: Election) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in e.candidates:
        b.button(text=c.name, callback_data=f"pick:{c.id}")
    b.adjust(1)
    return b.as_markup()


def confirm_kb(candidate_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=texts.BTN_CONFIRM, callback_data=f"vote:{candidate_id}")
    b.button(text=texts.BTN_BACK, callback_data="back")
    b.adjust(2)
    return b.as_markup()


def broadcast_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=texts.BTN_SEND, callback_data="bc:send")
    b.button(text=texts.BTN_CANCEL, callback_data="bc:cancel")
    b.adjust(2)
    return b.as_markup()


async def send_programs(bot: Bot, chat_id: int, e: Election, db: Storage) -> bool:
    """send the candidates' program files as one album; telegram file ids are cached so each file uploads once"""
    docs = [c for c in e.candidates if c.program and c.program.is_file()]
    if not docs:
        return False
    keys, media = [], []
    for c in docs:
        assert c.program is not None
        st = c.program.stat()
        # a replaced file gets a new key and is uploaded again
        key = f"file:{c.id}:{st.st_size}:{int(st.st_mtime)}"
        file_id = db.kv_get(key)
        keys.append(key)
        media.append(
            InputMediaDocument(media=file_id or FSInputFile(c.program, filename=c.program.name), caption=c.name)
        )
    if len(media) == 1:
        sent = [await bot.send_document(chat_id, media[0].media, caption=media[0].caption)]
    else:
        sent = await bot.send_media_group(chat_id, media)
    for key, m in zip(keys, sent):
        if m.document:
            db.kv_set(key, m.document.file_id)
    return True


async def safe_send(bot: Bot, db: Storage, chat_id: int, text: str, kb: InlineKeyboardMarkup | None = None) -> bool:
    for attempt in range(4):
        try:
            await bot.send_message(chat_id, text, reply_markup=kb)
            return True
        except TelegramRetryAfter as err:
            log.warning("flood control, sleeping %ss", err.retry_after)
            await asyncio.sleep(err.retry_after + 1)
        except TelegramForbiddenError:
            db.mark_blocked(chat_id)
            return False
        except TelegramBadRequest as err:
            log.warning("send to %s failed: %s", chat_id, err.message)
            return False
        except TelegramNetworkError as err:
            log.warning("network error on send to %s (attempt %d): %s", chat_id, attempt + 1, err)
            await asyncio.sleep(2)
    return False


async def send_many(
    bot: Bot,
    db: Storage,
    users: list[User],
    make: Callable[[User], tuple[str, InlineKeyboardMarkup | None]],
) -> tuple[int, int]:
    ok = failed = 0
    for u in users:
        text, kb = make(u)
        if await safe_send(bot, db, u.user_id, text, kb):
            ok += 1
        else:
            failed += 1
        await asyncio.sleep(SEND_INTERVAL)
    return ok, failed


async def _notify_open(bot: Bot, db: Storage, settings: Settings) -> None:
    e = settings.election
    users = db.recipients(not_voted_only=True)
    log.info("open notice: %d recipients", len(users))
    ok, failed = await send_many(bot, db, users, lambda u: (texts.opened_notice(e), ballot_kb(e)))
    log.info("open notice: sent %d, failed %d", ok, failed)


async def _notify_reminder(bot: Bot, db: Storage, settings: Settings) -> None:
    e = settings.election
    users = db.recipients(not_voted_only=True)
    log.info("reminder: %d recipients", len(users))
    ok, failed = await send_many(bot, db, users, lambda u: (texts.reminder_notice(e, now_utc()), ballot_kb(e)))
    log.info("reminder: sent %d, failed %d", ok, failed)


async def _notify_close(bot: Bot, db: Storage, settings: Settings) -> None:
    e = settings.election
    now = now_utc()
    text = texts.results(e, e.phase(now), db.tally(), db.stats().annulled, now)
    for admin_id in sorted(settings.admin_ids):
        await safe_send(bot, db, admin_id, text)
    log.info("close notice sent to %d admins", len(settings.admin_ids))


async def run_notices(bot: Bot, db: Storage, settings: Settings, clock: Callable[[], datetime] = now_utc) -> None:
    """send each notice once at its time; a mark in the kv table keeps a restart from sending it again"""
    e = settings.election
    events: list[tuple[str, datetime, Phase, Callable[[Bot, Storage, Settings], Awaitable[None]]]] = [
        ("open", e.start, Phase.OPEN, _notify_open),
    ]
    if e.reminder:
        events.append(("reminder", e.reminder, Phase.OPEN, _notify_reminder))
    events.append(("close", e.end, Phase.CLOSED, _notify_close))

    for key, at, phase, send in events:
        if db.kv_get(f"sent:{key}"):
            continue
        # short sleeps so a clock step on the host cannot make us miss the moment
        while (left := (at - clock()).total_seconds()) > 0:
            await asyncio.sleep(min(left, 30))
        now = clock()
        if e.phase(now) is not phase:
            log.info("notice %s skipped: its phase is over", key)
            continue
        db.kv_set(f"sent:{key}", now.isoformat())
        try:
            await send(bot, db, settings)
        except Exception:
            log.exception("notice %s failed", key)
