"""bot startup: dispatcher, command menus and the notice task"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from . import admin, user
from .config import Settings
from .db import Storage
from .messaging import run_notices

log = logging.getLogger(__name__)

USER_COMMANDS = [
    BotCommand(command="start", description="Статус голосования"),
    BotCommand(command="programs", description="Программы кандидатов"),
    BotCommand(command="help", description="Справка"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="results", description="Итоги голосования"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="voters", description="Список участников (CSV)"),
    BotCommand(command="annul", description="Аннулировать голос: /annul <id>"),
    BotCommand(command="broadcast", description="Сообщение всем: /broadcast <текст>"),
    BotCommand(command="admin", description="Команды администратора"),
]


async def setup_commands(bot: Bot, settings: Settings) -> None:
    await bot.set_my_commands(USER_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in settings.admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except TelegramBadRequest as err:
            # the admin has not opened the bot yet
            log.warning("admin menu for %s not set: %s", admin_id, err.message)


async def run(settings: Settings) -> None:
    db = Storage(settings.db_path)
    bot = Bot(settings.token, default=DefaultBotProperties(link_preview_is_disabled=True))
    dp = Dispatcher(db=db, settings=settings)
    # admin first: the user router ends with a catch-all handler
    dp.include_routers(admin.router, user.router)

    me = await bot.get_me()
    e = settings.election
    log.info(
        "bot @%s, election %r, %s .. %s, %d candidates, %d admins",
        me.username, e.title, e.start.isoformat(), e.end.isoformat(), len(e.candidates), len(settings.admin_ids),
    )
    await setup_commands(bot, settings)
    notices = asyncio.create_task(run_notices(bot, db, settings))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        notices.cancel()
        db.close()
