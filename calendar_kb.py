"""Календарь-кнопки для Телеграма: выбор диапазона дат в два тапа.

Первый тап — дата «с», второй — дата «по». Прошедшие даты неактивны,
листание по месяцам стрелками.

callback_data:
  cal:nav:ГГГГ-ММ   — листать на другой месяц
  cal:day:ГГГГ-ММ-ДД — тап по числу
  cal:x             — пустая кнопка (ничего не делает)
"""
import calendar as pycal
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
             "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

MAX_MONTHS_AHEAD = 12   # на сколько месяцев вперёд можно листать


def _shift(y: int, m: int, delta: int):
    idx = (y * 12 + (m - 1)) + delta
    return idx // 12, idx % 12 + 1


def build_calendar(year: int, month: int, start: str | None = None) -> InlineKeyboardMarkup:
    """Рисует сетку месяца. start — уже выбранная дата «с» (подсветится)."""
    today = date.today()
    rows = []

    # шапка: ◀ Июль 2026 ▶
    py, pm = _shift(year, month, -1)
    ny, nm = _shift(year, month, +1)
    limit = date(today.year, today.month, 1)
    max_y, max_m = _shift(today.year, today.month, MAX_MONTHS_AHEAD)

    back_ok = date(py, pm, 1) >= limit
    fwd_ok = date(ny, nm, 1) <= date(max_y, max_m, 1)
    rows.append([
        InlineKeyboardButton(text="◀" if back_ok else " ",
                             callback_data=f"cal:nav:{py:04d}-{pm:02d}" if back_ok else "cal:x"),
        InlineKeyboardButton(text=f"{MONTHS_RU[month - 1]} {year}", callback_data="cal:x"),
        InlineKeyboardButton(text="▶" if fwd_ok else " ",
                             callback_data=f"cal:nav:{ny:04d}-{nm:02d}" if fwd_ok else "cal:x"),
    ])

    rows.append([InlineKeyboardButton(text=d, callback_data="cal:x") for d in WEEKDAYS_RU])

    for week in pycal.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:x"))
                continue
            d = date(year, month, day)
            iso = d.isoformat()
            if d < today:                      # прошедшее — неактивно
                row.append(InlineKeyboardButton(text="·", callback_data="cal:x"))
            elif start and iso == start:       # выбранная дата «с»
                row.append(InlineKeyboardButton(text=f"[{day}]", callback_data=f"cal:day:{iso}"))
            else:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal:day:{iso}"))
        rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
