"""voter side: registration on first message, status, programs and the two-step vote"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import texts
from .config import Phase, Settings, now_utc
from .db import Storage, User, VoteResult
from .messaging import ballot_kb, confirm_kb, send_programs

log = logging.getLogger(__name__)

router = Router(name="user")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)


async def send_status(message: Message, db: Storage, settings: Settings, user: User, phase: Phase) -> None:
    e = settings.election
    if phase is Phase.BEFORE:
        await message.answer(texts.not_started(e))
    elif phase is Phase.CLOSED:
        await message.answer(texts.CLOSED)
    elif user.banned:
        await message.answer(texts.BANNED)
    elif db.has_voted(user.user_id):
        await message.answer(texts.ALREADY_VOTED)
    else:
        await message.answer(texts.ballot(e), reply_markup=ballot_kb(e))


@router.message(Command("programs"))
async def programs(message: Message, bot: Bot, db: Storage, settings: Settings) -> None:
    if not await send_programs(bot, message.chat.id, settings.election, db):
        await message.answer(texts.NO_PROGRAMS)


@router.message(Command("help"))
async def help_(message: Message, settings: Settings) -> None:
    await message.answer(texts.help_text(settings.election))


@router.message()
async def any_message(message: Message, bot: Bot, db: Storage, settings: Settings) -> None:
    """/start and any other message: register a new user and greet, or show the current status"""
    if message.from_user is None:
        return
    e = settings.election
    now = now_utc()
    phase = e.phase(now)
    uid = message.from_user.id

    # registration closes together with voting
    if phase is Phase.CLOSED and db.get_user(uid) is None:
        await message.answer(texts.CLOSED)
        return

    user, created = db.touch_user(uid, message.from_user.username, message.from_user.full_name, now)
    if not created:
        await send_status(message, db, settings, user, phase)
        return

    log.info("registered user %s", uid)
    await message.answer(texts.welcome(e, phase))
    try:
        await send_programs(bot, message.chat.id, e, db)
    except TelegramBadRequest as err:
        log.warning("program files not sent: %s", err.message)
    if phase is Phase.OPEN:
        await send_status(message, db, settings, user, phase)


async def _edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """replace the button message; messages older than 48 hours cannot be edited, so send a new one"""
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_text(text, reply_markup=kb)
            return
        except TelegramBadRequest as err:
            if "message is not modified" in err.message:
                return
            log.warning("edit failed: %s", err.message)
    if cb.bot:
        await cb.bot.send_message(cb.from_user.id, text, reply_markup=kb)


async def _closed_or_early(cb: CallbackQuery, settings: Settings, phase: Phase) -> bool:
    if phase is Phase.BEFORE:
        await cb.answer(texts.not_started(settings.election), show_alert=True)
        return True
    if phase is Phase.CLOSED:
        await _edit(cb, texts.CLOSED)
        await cb.answer()
        return True
    return False


@router.callback_query(F.data.startswith("pick:"))
async def pick(cb: CallbackQuery, settings: Settings) -> None:
    e = settings.election
    if await _closed_or_early(cb, settings, e.phase(now_utc())):
        return
    c = e.candidate((cb.data or "").split(":", 1)[1])
    if c is None:
        await cb.answer(texts.UNKNOWN_CANDIDATE, show_alert=True)
        return
    await _edit(cb, texts.confirm(c.name), confirm_kb(c.id))
    await cb.answer()


@router.callback_query(F.data == "back")
async def back(cb: CallbackQuery, settings: Settings) -> None:
    e = settings.election
    if await _closed_or_early(cb, settings, e.phase(now_utc())):
        return
    await _edit(cb, texts.ballot(e), ballot_kb(e))
    await cb.answer()


@router.callback_query(F.data.startswith("vote:"))
async def vote(cb: CallbackQuery, db: Storage, settings: Settings) -> None:
    e = settings.election
    # the time is checked when the vote arrives, not when the buttons were sent
    now = now_utc()
    if await _closed_or_early(cb, settings, e.phase(now)):
        return
    c = e.candidate((cb.data or "").split(":", 1)[1])
    if c is None:
        await cb.answer(texts.UNKNOWN_CANDIDATE, show_alert=True)
        return
    u = cb.from_user
    db.touch_user(u.id, u.username, u.full_name, now)
    result = db.cast_vote(u.id, c.id, now)
    # the log never carries the chosen candidate
    log.info("vote from %s: %s", u.id, result.value)
    text = {
        VoteResult.OK: texts.VOTED,
        VoteResult.ALREADY: texts.ALREADY_VOTED,
        VoteResult.BANNED: texts.BANNED,
        VoteResult.NOT_REGISTERED: texts.CLOSED,
    }[result]
    await _edit(cb, text)
    await cb.answer()
