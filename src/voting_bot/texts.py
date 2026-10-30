"""every text the bot sends; all user-facing strings are Russian"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from .config import Election, Phase
from .db import Stats, User

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def tz_label(e: Election) -> str:
    return "по московскому времени" if e.tz.key == "Europe/Moscow" else f"({e.tz.key})"


def fmt_day(e: Election, dt: datetime) -> str:
    local = dt.astimezone(e.tz)
    return f"{WEEKDAYS[local.weekday()]}, {local.day} {MONTHS[local.month - 1]}"


def fmt_time(e: Election, dt: datetime) -> str:
    return dt.astimezone(e.tz).strftime("%H:%M")


def fmt_window(e: Election) -> str:
    """e.g. 'в понедельник, 2 ноября, с 10:00 до 18:00 по московскому времени'"""
    start, end = e.start.astimezone(e.tz), e.end.astimezone(e.tz)
    if start.date() == end.date():
        return f"в {fmt_day(e, start)}, с {fmt_time(e, start)} до {fmt_time(e, end)} {tz_label(e)}"
    return f"с {fmt_day(e, start)}, {fmt_time(e, start)} до {fmt_day(e, end)}, {fmt_time(e, end)} {tz_label(e)}"


def fmt_stamp(e: Election, dt: datetime) -> str:
    return dt.astimezone(e.tz).strftime("%d.%m %H:%M")


def candidate_list(e: Election) -> str:
    lines = []
    for i, c in enumerate(e.candidates, 1):
        lines.append(f"{i}. {c.name}" + (f", {c.about}" if c.about else ""))
    return "\n".join(lines)


# user side

def welcome(e: Election, phase: Phase) -> str:
    if phase is Phase.BEFORE:
        status = "Вы зарегистрированы. Когда голосование начнётся, бот пришлёт сообщение с кнопками."
    else:
        status = "Вы зарегистрированы. Голосование уже идёт, кнопки для голоса - в следующем сообщении."
    return (
        f"Здравствуйте! Это бот для голосования: {e.title}.\n\n"
        f"Кандидаты:\n{candidate_list(e)}\n\n"
        f"Голосование пройдёт {fmt_window(e)}. "
        "У каждого участника один голос, изменить его после подтверждения нельзя.\n\n"
        f"{status}\n\n"
        f"Исходный код бота открыт: {e.source_url}\n\n"
        "Программы кандидатов - в файлах ниже."
    )


CLOSED = "Голосование завершено. Спасибо за участие!"
ALREADY_VOTED = "Вы уже проголосовали. Изменить голос нельзя."
VOTED = "Ваш голос учтён. Спасибо за участие!"
BANNED = "Ваш голос аннулирован организаторами. Повторно проголосовать нельзя."
UNKNOWN_CANDIDATE = "Такого кандидата нет. Выберите кандидата из списка."
NO_PROGRAMS = "Программы кандидатов пока не загружены."
NOT_ADMIN = "Вы не администратор."
BTN_CONFIRM = "Подтвердить"
BTN_BACK = "Назад"
BTN_SEND = "Отправить"
BTN_CANCEL = "Отмена"


def help_text(e: Election) -> str:
    return (
        f"Голосование пройдёт {fmt_window(e)}.\n\n"
        "Команды:\n"
        "/start - текущий статус голосования\n"
        "/programs - программы кандидатов\n"
        "/help - эта справка\n\n"
        f"Исходный код бота: {e.source_url}"
    )


def not_started(e: Election) -> str:
    return f"Вы зарегистрированы. Голосование ещё не началось: оно пройдёт {fmt_window(e)}."


def ballot(e: Election) -> str:
    return f"Голосование открыто до {fmt_time(e, e.end)} {tz_label(e)}.\nВыберите кандидата:"


def confirm(name: str) -> str:
    return f"Вы выбрали: {name}.\n\nПосле подтверждения изменить голос нельзя. Подтвердить?"


def opened_notice(e: Election) -> str:
    return f"Голосование открыто! Оно идёт до {fmt_time(e, e.end)} {tz_label(e)}.\nВыберите кандидата:"


def reminder_notice(e: Election, now: datetime) -> str:
    minutes = max(1, round((e.end - now).total_seconds() / 60))
    left = f"{minutes // 60} ч {minutes % 60:02d} мин" if minutes >= 60 else f"{minutes} мин"
    return (
        f"До конца голосования осталось {left} (до {fmt_time(e, e.end)} {tz_label(e)}).\n"
        "Вы ещё не проголосовали. Выберите кандидата:"
    )


# admin side

ADMIN_HELP = (
    "Команды администратора:\n"
    "/results - итоги голосования\n"
    "/stats - статистика\n"
    "/voters - список участников (CSV)\n"
    "/annul <id> - аннулировать голос участника\n"
    "/broadcast <текст> - сообщение всем участникам\n"
    "/admin - эта справка"
)
ANNUL_USAGE = "Использование: /annul <id>\nid участника есть в файле /voters."
BROADCAST_USAGE = "Использование: /broadcast <текст сообщения>"
BROADCAST_CANCELLED = "Рассылка отменена."
BROADCAST_EXPIRED = "Нет рассылки для отправки. Отправьте /broadcast <текст> ещё раз."


def phase_line(e: Election, phase: Phase) -> str:
    return {
        Phase.BEFORE: f"Голосование ещё не началось, оно пройдёт {fmt_window(e)}.",
        Phase.OPEN: f"Голосование идёт, до конца {fmt_time(e, e.end)}.",
        Phase.CLOSED: "Голосование завершено.",
    }[phase]


def results(e: Election, phase: Phase, tally: dict[str, int], annulled: int, now: datetime) -> str:
    if phase is not Phase.CLOSED and not e.admin_live_results:
        return f"Результаты будут доступны после окончания голосования ({fmt_time(e, e.end)} {tz_label(e)})."
    total = sum(tally.values())
    if phase is Phase.CLOSED:
        head = "Итоги голосования (окончательные):"
    else:
        head = f"Промежуточные итоги на {fmt_stamp(e, now)}:"
    lines = [head]
    for c in sorted(e.candidates, key=lambda c: -tally.get(c.id, 0)):
        n = tally.get(c.id, 0)
        pct = f"{100 * n / total:.1f}%" if total else "0%"
        lines.append(f"{c.name}: {n} ({pct})")
    lines.append(f"\nВсего голосов: {total}")
    if annulled:
        lines.append(f"Аннулировано участников: {annulled}")
    if phase is Phase.BEFORE:
        lines.append(phase_line(e, phase))
    return "\n".join(lines)


def stats(e: Election, phase: Phase, s: Stats, now: datetime) -> str:
    turnout = f" ({100 * s.voted / s.registered:.0f}% от зарегистрированных)" if s.registered else ""
    lines = [
        f"Статистика на {fmt_stamp(e, now)}",
        phase_line(e, phase),
        "",
        f"Зарегистрировались: {s.registered}",
        f"Проголосовали: {s.voted}{turnout}",
        f"Аннулировано участников: {s.annulled}",
        f"Заблокировали бота: {s.blocked}",
    ]
    if s.vote_times:
        per_hour = Counter(t.astimezone(e.tz).strftime("%d.%m %H") for t in s.vote_times)
        lines += ["", "Голоса по часам:"]
        for key, n in sorted(per_hour.items()):
            day, h = key.split()
            lines.append(f"{day} {h}:00-{(int(h) + 1) % 24:02d}:00: {n}")
    return "\n".join(lines)


def user_label(user: User) -> str:
    name = user.tg_name or ""
    if user.username:
        name = f"{name} @{user.username}".strip()
    return f"{name} (id {user.user_id})".strip()


def annul_done(user: User, had_vote: bool) -> str:
    if had_vote:
        return f"Голос участника {user_label(user)} аннулирован. Повторно проголосовать участник не сможет."
    return f"У участника {user_label(user)} не было голоса. Теперь участник не сможет проголосовать."


def annul_unknown(user_id: int) -> str:
    return f"Участник с id {user_id} не найден."


def broadcast_confirm(text: str, n: int) -> str:
    return f"Отправить это сообщение всем участникам? Получателей: {n}.\n\n{text}"


def broadcast_started(n: int) -> str:
    return f"Рассылка начата. Получателей: {n}."


def broadcast_done(ok: int, failed: int) -> str:
    return f"Рассылка завершена: доставлено {ok}, не доставлено {failed}."
