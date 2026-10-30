"""admin commands; anyone not in ADMIN_IDS gets a refusal"""

from __future__ import annotations

import asyncio
import io
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.utils import get_column_letter

from . import texts
from .config import Settings, now_utc
from .db import Storage
from .messaging import broadcast_kb, send_many

log = logging.getLogger(__name__)

ADMIN_COMMANDS = ("admin", "results", "stats", "voters", "annul", "broadcast")

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)

# broadcast text waiting for the admin's confirmation, by admin id
_pending: dict[int, str] = {}
# strong references, so running broadcasts are not garbage collected
_tasks: set[asyncio.Task] = set()


class IsAdmin(Filter):
    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        return settings.is_admin(event.from_user.id if event.from_user else None)


@router.message(Command("admin"), IsAdmin())
async def admin_help(message: Message) -> None:
    await message.answer(texts.ADMIN_HELP)


@router.message(Command("results"), IsAdmin())
async def results(message: Message, db: Storage, settings: Settings) -> None:
    e, now = settings.election, now_utc()
    await message.answer(texts.results(e, e.phase(now), db.tally(), db.stats().annulled, now))


@router.message(Command("stats"), IsAdmin())
async def stats(message: Message, db: Storage, settings: Settings) -> None:
    e, now = settings.election, now_utc()
    await message.answer(texts.stats(e, e.phase(now), db.stats(), now))


VOTERS_HEADER = [
    ("id", 13),
    ("username", 22),
    ("имя в Telegram", 30),
    ("время регистрации", 20),
    ("проголосовал", 14),
    ("аннулирован", 13),
    ("заблокировал бота", 19),
]


def _text(value: str) -> str:
    return ILLEGAL_CHARACTERS_RE.sub("", value)


def voters_xlsx(db: Storage, settings: Settings) -> bytes:
    """who registered and whether they voted; the choice is never exported"""
    tz = settings.election.tz
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "участники"
    ws.append([name for name, _ in VOTERS_HEADER])
    for row in db.voters():
        u = row.user
        ws.append([
            u.user_id,
            _text(f"@{u.username}") if u.username else "",
            _text(u.tg_name or ""),
            u.started_at.astimezone(tz).replace(tzinfo=None),
            "да" if row.voted else "нет",
            "да" if u.banned else "нет",
            "да" if u.blocked else "нет",
        ])
    for cells in ws.iter_rows():
        for cell in cells:
            # usernames and telegram names are typed by users: a leading "=", "@", "+" or "-"
            # must stay plain text and never become a formula
            if isinstance(cell.value, str):
                cell.data_type = "s"
        cells[3].number_format = "yyyy-mm-dd hh:mm:ss"
    for i, (_, width) in enumerate(VOTERS_HEADER, 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@router.message(Command("voters"), IsAdmin())
async def voters(message: Message, db: Storage, settings: Settings) -> None:
    stamp = now_utc().astimezone(settings.election.tz).strftime("%Y%m%d_%H%M")
    await message.answer_document(BufferedInputFile(voters_xlsx(db, settings), filename=f"voters_{stamp}.xlsx"))


@router.message(Command("annul"), IsAdmin())
async def annul(message: Message, command: CommandObject, db: Storage) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        await message.answer(texts.ANNUL_USAGE)
        return
    user_id = int(arg)
    assert message.from_user is not None
    had_vote = db.annul(user_id, message.from_user.id, now_utc())
    if had_vote is None:
        await message.answer(texts.annul_unknown(user_id))
        return
    log.info("admin %s annulled user %s (had vote: %s)", message.from_user.id, user_id, had_vote)
    user = db.get_user(user_id)
    assert user is not None
    await message.answer(texts.annul_done(user, had_vote))


@router.message(Command("broadcast"), IsAdmin())
async def broadcast(message: Message, command: CommandObject, db: Storage) -> None:
    text = (command.args or "").strip()
    if not text:
        await message.answer(texts.BROADCAST_USAGE)
        return
    assert message.from_user is not None
    _pending[message.from_user.id] = text
    n = len(db.recipients(not_voted_only=False))
    await message.answer(texts.broadcast_confirm(text, n), reply_markup=broadcast_kb())


@router.callback_query(F.data == "bc:cancel", IsAdmin())
async def broadcast_cancel(cb: CallbackQuery) -> None:
    _pending.pop(cb.from_user.id, None)
    if isinstance(cb.message, Message):
        await cb.message.edit_text(texts.BROADCAST_CANCELLED)
    await cb.answer()


@router.callback_query(F.data == "bc:send", IsAdmin())
async def broadcast_send(cb: CallbackQuery, bot: Bot, db: Storage) -> None:
    text = _pending.pop(cb.from_user.id, None)
    if text is None:
        await cb.answer(texts.BROADCAST_EXPIRED, show_alert=True)
        return
    users = db.recipients(not_voted_only=False)
    if isinstance(cb.message, Message):
        await cb.message.edit_text(texts.broadcast_started(len(users)))
    await cb.answer()
    log.info("admin %s started a broadcast to %d users", cb.from_user.id, len(users))

    async def run() -> None:
        ok, failed = await send_many(bot, db, users, lambda u: (text, None))
        log.info("broadcast done: sent %d, failed %d", ok, failed)
        await bot.send_message(cb.from_user.id, texts.broadcast_done(ok, failed))

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# registered last: the same commands from anyone else
@router.message(Command(*ADMIN_COMMANDS))
async def not_admin(message: Message) -> None:
    await message.answer(texts.NOT_ADMIN)


@router.callback_query(F.data.startswith("bc:"))
async def not_admin_cb(cb: CallbackQuery) -> None:
    await cb.answer(texts.NOT_ADMIN, show_alert=True)
