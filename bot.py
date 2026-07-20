"""Телеграм-бот отслеживания цен на авиабилеты.

Умеет:
  - вылет из любого города мира, включая всю Россию;
  - до 3 городов назначения в одной подписке (можно из разных стран);
  - в одну сторону ИЛИ туда-обратно (цена ОБЩАЯ за весь перелёт);
  - туда-обратно задаётся длительностью: "поездка на 10-14 ночей" —
    бот сам перебирает удачные пары дат;
  - окно вылета выбирается календарём;
  - сигнал в личку с партнёрской ссылкой;
  - автопостинг лучших находок в публичный канал (для роста аудитории).

Команды: /start /track /list /stop N /postnow (админ) /stats (админ)
"""
import asyncio
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import aviasales
import calendar_kb
import config
import db
import deals as deals_mod

router = Router()
BOT_USERNAME = ""     # заполняется при старте


def money(n) -> str:
    return f"{n:,}".replace(",", " ")


def fmt_ru(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


async def stale(call: CallbackQuery, state: FSMContext):
    """Кнопка из старого сообщения — бот перезапускался, данные потерялись."""
    await state.clear()
    await call.answer("Кнопка устарела", show_alert=True)
    await call.message.answer(
        "⏳ Эта кнопка из старого сеанса — бот перезапускался, "
        "и я забыл, о чём мы говорили.\n\n"
        "Начни заново: /track")


def with_cancel(kb: InlineKeyboardBuilder, cols=1) -> InlineKeyboardMarkup:
    """Добавляет кнопку «Начать заново» к любой клавиатуре."""
    kb.button(text="❌ Начать заново", callback_data="cancel")
    kb.adjust(cols)
    return kb.as_markup()


MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✈️ Новый поиск")],
        [KeyboardButton(text="📋 Мои отслеживания"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Жми кнопку или пиши команду")


HELP_TEXT = (
    "❓ <b>Как это работает</b>\n\n"
    "Ты задаёшь маршрут, даты и максимальную цену. Я проверяю цены раз в час "
    "и пишу, как только билет подешевеет до твоего порога.\n\n"
    "<b>Кнопки внизу:</b>\n"
    "✈️ Новый поиск — создать отслеживание\n"
    "📋 Мои отслеживания — список и удаление\n\n"
    "<b>Команды:</b>\n"
    "/track — создать отслеживание\n"
    "/list — список (там же кнопки 🗑 для удаления)\n"
    "/cancel — сбросить и начать заново\n\n"
    "<b>Что можно задать:</b>\n"
    "• любой город мира и вся Россия\n"
    f"• до {config.MAX_DEST_CITIES} городов назначения сразу\n"
    "• туда-обратно (цена за весь перелёт) или в одну сторону\n"
    "• только прямые или с пересадками\n"
    "• окно дат: например с 15 по 25 июля\n"
    "• длительность поездки: 10–14 ночей\n\n"
    "<b>Важно:</b> цены берутся из кэша Aviasales, поэтому в сигнале есть "
    "кнопка — жми её и смотри актуальную цену. Дешёвые билеты разбирают быстро."
)


class Track(StatesGroup):
    origin = State()
    dests = State()
    trip_type = State()
    direct = State()
    dates = State()
    nights = State()
    price = State()


# ================= /start =================

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "✈️ <b>Привет! Я слежу за ценами на авиабилеты.</b>\n\n"
        "Задаёшь маршрут, даты и максимальную цену — я проверяю и пишу, "
        "как только билет дешевеет до твоего порога.\n\n"
        "🔹 Любой город мира <b>и вся Россия</b>\n"
        f"🔹 До <b>{config.MAX_DEST_CITIES} городов</b> в одной подписке — "
        "хоть из разных стран\n"
        "🔹 <b>Туда-обратно</b> — цена сразу за весь перелёт\n"
        "🔹 <b>Только прямые</b> или с пересадками — на выбор\n"
        "🔹 Окно дат календарём + длительность поездки\n\n"
        "<b>Команды:</b>\n"
        "Жми <b>✈️ Новый поиск</b> внизу 👇",
        reply_markup=MAIN_KB)


# ================= отмена =================

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    cur = await state.get_state()
    await state.clear()
    if cur:
        await message.answer("❌ Отменил. Начать заново — /track")
    else:
        await message.answer("Нечего отменять.\n/track — создать отслеживание")


@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer("❌ Начинаем заново.\n\n"
                              "🛫 <b>Откуда летим?</b>\nНапиши город, например: <i>Москва</i>")
    await state.set_state(Track.origin)
    await call.answer()


# ================= шаг 1: откуда =================

async def start_track(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Track.origin)
    await message.answer("🛫 <b>Откуда летим?</b>\nНапиши город, например: <i>Москва</i>")


@router.message(Command("track"))
async def cmd_track(message: Message, state: FSMContext):
    await start_track(message, state)


# --- кнопки нижнего меню ---
# Важно: объявлены ДО обработчиков мастера, иначе нажатие кнопки посреди
# мастера примут за название города.

@router.message(F.text == "✈️ Новый поиск")
async def btn_track(message: Message, state: FSMContext):
    await start_track(message, state)


@router.message(F.text == "📋 Мои отслеживания")
async def btn_list(message: Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await message.answer("<i>Создание отслеживания отменено.</i>")
    await show_list(message)


@router.message(F.text == "❓ Помощь")
async def btn_help(message: Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await message.answer("<i>Создание отслеживания отменено.</i>")
    await message.answer(HELP_TEXT, reply_markup=MAIN_KB)


@router.message(Track.origin)
async def track_origin(message: Message, state: FSMContext):
    results = await aviasales.search_places(message.text.strip())
    if not results:
        await message.answer("Ничего не нашёл 🤔 Попробуй другое название.")
        return
    await state.update_data(origin_results=results)
    kb = InlineKeyboardBuilder()
    for i, p in enumerate(results):
        label = p["name"] + (f" ({p['country_name']})" if p.get("country_name") else "")
        kb.button(text=label, callback_data=f"org:{i}")
    await message.answer("Выбери пункт вылета:", reply_markup=with_cancel(kb))


@router.callback_query(F.data.startswith("org:"))
async def pick_origin(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    results = data.get("origin_results")
    idx = int(call.data.split(":")[1])
    if not results or idx >= len(results):
        return await stale(call, state)
    p = results[idx]
    await state.update_data(origin_code=p["code"], origin_name=p["name"], dests=[])
    await state.set_state(Track.dests)
    await call.message.edit_text(f"🛫 Вылет: <b>{p['name']}</b>")
    await call.message.answer(
        "🛬 <b>Куда летим?</b>\n"
        f"Напиши город. Можно добавить до {config.MAX_DEST_CITIES} городов — "
        "хоть из разных стран.\n\n"
        "<i>Например: Нячанг, потом Бангкок, потом Хошимин.</i>\n\n"
        "<i>Ошибся с вылетом? Жми /cancel</i>")
    await call.answer()


# ================= шаг 2: города назначения =================

def dests_keyboard(dests) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Дальше", callback_data="dst:done")
    if dests:
        kb.button(text="↩️ Убрать последний", callback_data="dst:undo")
    kb.button(text="🛫 Изменить вылет", callback_data="dst:reorigin")
    return with_cancel(kb)


def dests_text(dests) -> str:
    lst = "\n".join(f"  {i+1}. {d['name']}" for i, d in enumerate(dests))
    left = config.MAX_DEST_CITIES - len(dests)
    tail = (f"\n\nМожешь добавить ещё {left} — просто напиши город."
            if left > 0 else "\n\nЛимит городов достигнут.")
    return f"🛬 <b>Направления:</b>\n{lst}{tail}"


@router.message(Track.dests)
async def track_dests(message: Message, state: FSMContext):
    data = await state.get_data()
    if len(data.get("dests", [])) >= config.MAX_DEST_CITIES:
        await message.answer(f"Уже {config.MAX_DEST_CITIES} города — это максимум. Жми «Дальше».",
                             reply_markup=dests_keyboard(data["dests"]))
        return
    results = await aviasales.search_places(message.text.strip())
    if not results:
        await message.answer("Ничего не нашёл 🤔 Попробуй другое название.")
        return
    await state.update_data(dest_results=results)
    kb = InlineKeyboardBuilder()
    for i, p in enumerate(results):
        label = p["name"] + (f" ({p['country_name']})" if p.get("country_name") else "")
        kb.button(text=label, callback_data=f"dst:add:{i}")
    await message.answer("Выбери город назначения:", reply_markup=with_cancel(kb))


@router.callback_query(F.data.startswith("dst:add:"))
async def add_dest(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    results = data.get("dest_results")
    idx = int(call.data.split(":")[2])
    if not results or idx >= len(results) or "origin_code" not in data:
        return await stale(call, state)
    p = results[idx]
    dests = data.get("dests", [])
    if any(d["code"] == p["code"] for d in dests):
        await call.answer("Этот город уже добавлен", show_alert=True)
        return
    if len(dests) >= config.MAX_DEST_CITIES:
        await call.answer("Достигнут лимит", show_alert=True)
        return
    dests.append({"code": p["code"], "name": p["name"]})
    await state.update_data(dests=dests)
    await call.message.edit_text(dests_text(dests), reply_markup=dests_keyboard(dests))
    await call.answer("Добавлено ✅")


@router.callback_query(F.data == "dst:undo")
async def undo_dest(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    dests = data.get("dests", [])
    if dests:
        dests.pop()
        await state.update_data(dests=dests)
    if dests:
        await call.message.edit_text(dests_text(dests), reply_markup=dests_keyboard(dests))
    else:
        await call.message.edit_text("Список пуст. Напиши город.")
    await call.answer()


@router.callback_query(F.data == "dst:reorigin")
async def reorigin(call: CallbackQuery, state: FSMContext):
    await state.update_data(dests=[], origin_code=None, origin_name=None)
    await state.set_state(Track.origin)
    try:
        await call.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await call.message.answer(
        "🛫 <b>Откуда летим?</b>\nНапиши город, например: <i>Москва</i>")
    await call.answer()


@router.callback_query(F.data == "dst:done")
async def dests_done(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "origin_code" not in data:
        return await stale(call, state)
    if not data.get("dests"):
        await call.answer("Добавь хотя бы один город", show_alert=True)
        return
    await state.set_state(Track.trip_type)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔁 Туда-обратно", callback_data="tt:round")
    kb.button(text="➡️ В одну сторону", callback_data="tt:oneway")
    await call.message.answer(
        "🎫 <b>Какой билет ищем?</b>\n\n"
        "<i>Туда-обратно — цена будет считаться сразу за весь перелёт "
        "(так их и продают, часто дешевле двух односторонних).</i>",
        reply_markup=with_cancel(kb))
    await call.answer()


# ================= шаг 3: тип билета =================

@router.callback_query(F.data.startswith("tt:"))
async def pick_trip_type(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("dests"):
        return await stale(call, state)
    tt = call.data.split(":")[1]
    await state.update_data(trip_type=tt, date_from=None)
    await state.set_state(Track.direct)
    label = "🔁 Туда-обратно" if tt == "round" else "➡️ В одну сторону"
    await call.message.edit_text(f"🎫 Тип: <b>{label}</b>")
    kb = InlineKeyboardBuilder()
    kb.button(text="✈️ Только прямые", callback_data="dir:1")
    kb.button(text="🔄 Любые (можно с пересадками)", callback_data="dir:0")
    await call.message.answer(
        "🛩 <b>Прямые рейсы или с пересадками?</b>\n\n"
        "<i>Только прямые — удобнее, но дороже и вариантов меньше.\n"
        "Любые — сюда попадут и прямые тоже, просто дешёвых находок будет больше.</i>",
        reply_markup=with_cancel(kb))
    await call.answer()


# ================= шаг 4: прямые или с пересадками =================

@router.callback_query(F.data.startswith("dir:"))
async def pick_direct(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("trip_type"):
        return await stale(call, state)
    direct = call.data.split(":")[1] == "1"
    await state.update_data(direct=direct)
    await state.set_state(Track.dates)
    label = "✈️ Только прямые" if direct else "🔄 Любые"
    await call.message.edit_text(f"🛩 Рейсы: <b>{label}</b>")
    today = date.today()
    await call.message.answer(
        "📅 <b>Окно вылета «туда».</b>\n"
        "Первый тап — дата <b>«с»</b>, второй — дата <b>«по»</b>.\n"
        f"Максимум {config.MAX_WINDOW_DAYS} дней.\n\n"
        "<i>Начать заново — /cancel</i>",
        reply_markup=calendar_kb.build_calendar(today.year, today.month))
    await call.answer()


# ================= шаг 5: календарь =================

@router.callback_query(F.data == "cal:x")
async def cal_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("cal:nav:"))
async def cal_nav(call: CallbackQuery, state: FSMContext):
    y, m = call.data.split(":")[2].split("-")
    data = await state.get_data()
    await call.message.edit_reply_markup(
        reply_markup=calendar_kb.build_calendar(int(y), int(m), data.get("date_from")))
    await call.answer()


@router.callback_query(F.data.startswith("cal:day:"), Track.dates)
async def cal_day(call: CallbackQuery, state: FSMContext):
    picked = call.data.split(":")[2]
    data = await state.get_data()
    if not data.get("trip_type") or data.get("direct") is None:
        return await stale(call, state)
    start = data.get("date_from")

    if not start:
        await state.update_data(date_from=picked)
        d = date.fromisoformat(picked)
        await call.message.edit_text(
            f"📅 Вылет <b>с {fmt_ru(picked)}</b>\nТеперь выбери дату <b>«по»</b>:",
            reply_markup=calendar_kb.build_calendar(d.year, d.month, picked))
        await call.answer()
        return

    d1, d2 = date.fromisoformat(start), date.fromisoformat(picked)
    if d2 < d1:
        await state.update_data(date_from=picked)
        await call.message.edit_text(
            f"📅 Вылет <b>с {fmt_ru(picked)}</b>\nТеперь выбери дату <b>«по»</b>:",
            reply_markup=calendar_kb.build_calendar(d2.year, d2.month, picked))
        await call.answer("Начало сдвинуто")
        return

    span = (d2 - d1).days + 1
    if span > config.MAX_WINDOW_DAYS:
        await call.answer(f"Окно {span} дн. — максимум {config.MAX_WINDOW_DAYS}.",
                          show_alert=True)
        return

    await state.update_data(date_to=picked)
    await call.message.edit_text(
        f"📅 Окно вылета: <b>{fmt_ru(start)} — {fmt_ru(picked)}</b> ({span} дн.)")

    if data["trip_type"] == "round":
        await state.set_state(Track.nights)
        kb = InlineKeyboardBuilder()
        for lo, hi in ((2, 4), (5, 7), (7, 10), (10, 14), (14, 21), (21, 30)):
            kb.button(text=f"{lo}–{hi} ночей", callback_data=f"ng:{lo}:{hi}")
        kb.button(text="✏️ Своя длительность", callback_data="ng:custom")
        kb.button(text="❌ Начать заново", callback_data="cancel")
        kb.adjust(2, 2, 2, 1, 1)
        await call.message.answer(
            "🌙 <b>Сколько длится поездка?</b>\n"
            "Я переберу все удачные пары дат в этом диапазоне — "
            "дешёвые связки часто выпадают на неочевидные дни.",
            reply_markup=kb.as_markup())
    else:
        await state.set_state(Track.price)
        await ask_price(call.message, "oneway")
    await call.answer()


# ================= шаг 6: длительность =================

async def ask_price(message: Message, trip_type: str):
    what = ("за <b>весь перелёт туда-обратно</b>" if trip_type == "round"
            else "в одну сторону")
    await message.answer(
        f"💰 <b>Максимальная цена, ₽?</b> ({what})\n"
        "Пришли число, например <code>45000</code>.\n"
        "Сигнал придёт, только если билет дешевле.\n\n"
        "<i>Начать заново — /cancel</i>")


@router.callback_query(F.data.startswith("ng:"), Track.nights)
async def pick_nights(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    if parts[1] == "custom":
        await call.message.answer(
            "✏️ Напиши длительность двумя числами через дефис, "
            "например <code>10-14</code> (от 10 до 14 ночей).")
        await call.answer()
        return
    lo, hi = int(parts[1]), int(parts[2])
    await state.update_data(min_nights=lo, max_nights=hi)
    await state.set_state(Track.price)
    await call.message.edit_text(f"🌙 Длительность: <b>{lo}–{hi} ночей</b>")
    await ask_price(call.message, "round")
    await call.answer()


@router.message(Track.nights)
async def custom_nights(message: Message, state: FSMContext):
    raw = message.text.strip().replace(" ", "").replace("—", "-").replace("–", "-")
    parts = raw.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("Формат: <code>10-14</code> (две цифры через дефис).")
        return
    lo, hi = int(parts[0]), int(parts[1])
    if lo < 1 or hi < lo:
        await message.answer("Первое число должно быть меньше второго и больше нуля.")
        return
    if hi > config.MAX_NIGHTS:
        await message.answer(f"Максимум {config.MAX_NIGHTS} ночей.")
        return
    await state.update_data(min_nights=lo, max_nights=hi)
    await state.set_state(Track.price)
    await message.answer(f"🌙 Длительность: <b>{lo}–{hi} ночей</b>")
    await ask_price(message, "round")


# ================= шаг 7: цена =================

@router.message(Track.price)
async def track_price(message: Message, state: FSMContext, bot: Bot):
    raw = message.text.strip().replace(" ", "").replace("₽", "")
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Нужно число, например 45000.")
        return
    data = await state.get_data()
    if not data.get("origin_code") or not data.get("dests") or not data.get("date_to"):
        await state.clear()
        await message.answer("Что-то потерялось по дороге. Начни заново: /track")
        return
    tt = data["trip_type"]
    sub_id = db.add_subscription(
        user_id=message.from_user.id,
        origin_code=data["origin_code"], origin_name=data["origin_name"],
        dests=data["dests"], date_from=data["date_from"], date_to=data["date_to"],
        trip_type=tt, min_nights=data.get("min_nights"),
        max_nights=data.get("max_nights"), max_price=int(raw),
        direct=1 if data.get("direct") else 0)
    await state.clear()
    cities = ", ".join(d["name"] for d in data["dests"])
    extra = (f"\n🌙 поездка {data['min_nights']}–{data['max_nights']} ночей"
             if tt == "round" else "")
    kind = "🔁 туда-обратно" if tt == "round" else "➡️ в одну сторону"
    dir_label = "✈️ только прямые" if data.get("direct") else "🔄 любые рейсы"
    await message.answer(
        "✅ <b>Отслеживание создано!</b>\n\n"
        f"🛫 {data['origin_name']} → 🛬 {cities}\n"
        f"{kind} · {dir_label}\n"
        f"📅 вылет {fmt_ru(data['date_from'])} — {fmt_ru(data['date_to'])}{extra}\n"
        f"💰 не дороже {money(int(raw))} ₽\n\n"
        "Смотрю, что есть прямо сейчас...",
        reply_markup=MAIN_KB)

    # сразу проверяем — чтобы не ждать до следующего часа
    sub = db.get_subscription(sub_id)
    if sub is None:
        return
    best = await check_sub(bot, sub)

    if best is None:
        await message.answer(
            "🤷 <b>Пока ничего не нашёл на эти даты.</b>\n\n"
            "Обычно так бывает, если:\n"
            "• выбраны «только прямые», а их на маршруте нет\n"
            "• окно дат слишком узкое\n"
            "• для длительности нет подходящих пар дат\n\n"
            "Я всё равно буду проверять раз в час — цены появляются.")
        return

    if best["price"] <= sub["max_price"]:
        return          # сигнал уже ушёл в check_sub

    diff = best["price"] - sub["max_price"]
    dep = best["departure_at"]
    link = aviasales.build_link(sub["origin_code"], best["dest_code"], dep,
                                best.get("return_at"), direct=bool(sub["direct"]),
                                api_link=best.get("link"))
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔎 Посмотреть этот вариант", url=link)]])
    when = fmt_ru(dep)
    if sub["trip_type"] == "round" and best.get("return_at"):
        when += f" → {fmt_ru(best['return_at'])} ({best['nights']} "\
                f"{deals_mod.nights_word(best['nights'])})"
    await message.answer(
        f"📊 <b>Сейчас самое дешёвое:</b>\n\n"
        f"🛬 {best['dest_name']}\n"
        f"📅 {when}\n"
        f"💰 <b>{money(best['price'])} ₽</b>\n\n"
        f"Это на {money(diff)} ₽ дороже твоего порога "
        f"({money(sub['max_price'])} ₽).\n"
        f"Буду следить и напишу, когда упадёт 👌",
        reply_markup=kb, disable_web_page_preview=True)


# ================= /list, /stop =================

def build_list(user_id):
    """Собирает текст списка и кнопки удаления. Возвращает (текст, клавиатура)."""
    subs = db.list_subscriptions(user_id)
    if not subs:
        return None, None
    out = ["📋 <b>Твои отслеживания:</b>\n"]
    kb = InlineKeyboardBuilder()
    for n, s in enumerate(subs, 1):
        cities = ", ".join(d["name"] for d in db.parse_dests(s))
        kind = "🔁" if s["trip_type"] == "round" else "➡️"
        kind += " ✈️" if s["direct"] else ""
        extra = (f"  🌙 {s['min_nights']}–{s['max_nights']} ноч."
                 if s["trip_type"] == "round" else "")
        out.append(f"<b>{n}.</b> {kind} {s['origin_name']} → {cities}\n"
                   f"     📅 {fmt_ru(s['date_from'])} — {fmt_ru(s['date_to'])}{extra}\n"
                   f"     💰 ≤ {money(s['max_price'])} ₽")
        # короткая подпись на кнопке, чтобы влезала
        short = cities if len(cities) <= 18 else cities[:16] + "…"
        kb.button(text=f"🗑 {n}. {short}", callback_data=f"del:{s['id']}")
    kb.adjust(1)
    return "\n".join(out), kb.as_markup()


async def show_list(message: Message):
    text, kb = build_list(message.from_user.id)
    if not text:
        await message.answer("Пока пусто. Жми <b>✈️ Новый поиск</b> 👇",
                             reply_markup=MAIN_KB)
        return
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(call: CallbackQuery):
    sub_id = int(call.data.split(":")[1])
    ok = db.delete_subscription(sub_id, call.from_user.id)
    if not ok:
        await call.answer("Уже удалено", show_alert=True)
    else:
        await call.answer("🗑 Удалено")
    text, kb = build_list(call.from_user.id)
    if not text:
        await call.message.edit_text("📋 Список пуст.\n\nЖми <b>✈️ Новый поиск</b> 👇")
        return
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


@router.message(Command("list"))
async def cmd_list(message: Message):
    await show_list(message)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=MAIN_KB)


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Укажи номер: /stop 1")
        return
    ok = db.delete_subscription(int(parts[1]), message.from_user.id)
    await message.answer("🗑 Удалил." if ok else "Не нашёл такое отслеживание.")


# ================= админ =================

@router.message(Command("postnow"))
async def cmd_postnow(message: Message, bot: Bot):
    if message.from_user.id != config.ADMIN_ID:
        return
    if not config.CHANNEL_ID:
        await message.answer("CHANNEL_ID не задан в .env")
        return
    await message.answer("Проверяю маршруты для канала...")
    n = await deals_mod.check_and_post(bot, BOT_USERNAME, force=True)
    await message.answer(f"Готово. Опубликовано постов: {n}")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        return
    subs = db.all_active_subscriptions()
    await message.answer(f"👥 Пользователей: {db.count_users()}\n"
                         f"📋 Подписок: {len(subs)}")


# ================= фоновый мониторинг =================

async def find_best(s, cache=None):
    """Ищет самый дешёвый вариант по подписке (по всем её городам).

    Возвращает {price, departure_at, return_at, nights, dest_code, dest_name, ...}
    или None, если ничего не нашлось.
    """
    direct = bool(s["direct"])
    best = None
    for d in db.parse_dests(s):
        key = (s["origin_code"], d["code"], s["date_from"], s["date_to"],
               s["trip_type"], s["min_nights"], s["max_nights"], direct)
        try:
            if cache is not None and key in cache:
                res = cache[key]
            else:
                res = await aviasales.get_min_price(
                    s["origin_code"], d["code"], s["date_from"], s["date_to"],
                    s["trip_type"], s["min_nights"], s["max_nights"], direct=direct)
                if cache is not None:
                    cache[key] = res
                await asyncio.sleep(1)
        except Exception as e:
            print("  ! ошибка проверки:", e)
            continue
        if res and res.get("price") and (best is None or res["price"] < best["price"]):
            best = {**res, "dest_code": d["code"], "dest_name": d["name"]}
    return best


async def check_sub(bot: Bot, s, cache=None):
    """Проверяет одну подписку и шлёт сигнал, если цена ниже порога."""
    best = await find_best(s, cache)
    if best and best["price"] <= s["max_price"]:
        if s["last_notified"] is None or best["price"] < s["last_notified"]:
            try:
                await send_signal(bot, s, best)
                db.set_last_notified(s["id"], best["price"])
            except Exception as e:
                print("  ! не смог отправить сигнал:", e)
    elif s["last_notified"] is not None:
        db.set_last_notified(s["id"], None)
    return best


async def run_checks(bot: Bot):
    removed = db.delete_expired(date.today().isoformat())
    if removed:
        print(f"[monitor] удалено просроченных: {removed}")
    subs = db.all_active_subscriptions()
    if not subs:
        return
    print(f"[monitor] проверяю {len(subs)} подписок...")
    cache = {}
    for s in subs:
        await check_sub(bot, s, cache)


async def send_signal(bot: Bot, sub, best):
    dep = best["departure_at"]
    ret = best.get("return_at")
    is_direct = bool(sub["direct"])
    link = aviasales.build_link(sub["origin_code"], best["dest_code"], dep, ret,
                                direct=is_direct, api_link=best.get("link"))

    if sub["trip_type"] == "round" and ret:
        kind = "🔁 туда-обратно"
        when = (f"📅 {fmt_ru(dep)} → {fmt_ru(ret)} "
                f"({best['nights']} {deals_mod.nights_word(best['nights'])})")
    else:
        kind = "➡️ в одну сторону"
        when = f"📅 вылет {fmt_ru(dep)}"
    kind += " · ✈️ прямой" if is_direct else ""

    fresh = ""
    if best.get("found_at"):
        fresh = f"\n<i>цена найдена: {best['found_at'][:16].replace('T', ' ')} UTC</i>"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔎 Проверить и купить", url=link)]])
    await bot.send_message(
        sub["user_id"],
        f"🔥 <b>Цена упала!</b>\n\n"
        f"🛫 {sub['origin_name']} → 🛬 <b>{best['dest_name']}</b>\n"
        f"{kind}\n{when}\n"
        f"💰 <b>{money(best['price'])} ₽</b> "
        f"(твой порог: {money(sub['max_price'])} ₽){fresh}\n\n"
        f"⚠️ Цена из кэша Aviasales — жми кнопку и проверь актуальную. "
        f"Дешёвые тарифы разбирают быстро.",
        reply_markup=kb, disable_web_page_preview=True)


async def run_deals(bot: Bot):
    try:
        await deals_mod.check_and_post(bot, BOT_USERNAME)
    except Exception as e:
        print("Ошибка автопостинга:", e)


# ================= запуск =================

async def main():
    global BOT_USERNAME
    config.check_required()
    db.init_db()
    await aviasales.load_reference_data()

    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    me = await bot.get_me()
    BOT_USERNAME = me.username

    await bot.set_my_commands([
        BotCommand(command="track", description="✈️ Новый поиск"),
        BotCommand(command="list", description="📋 Мои отслеживания"),
        BotCommand(command="cancel", description="❌ Начать заново"),
        BotCommand(command="help", description="❓ Помощь"),
    ])

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    sched = AsyncIOScheduler()
    sched.add_job(run_checks, "interval", minutes=config.CHECK_INTERVAL_MIN, args=[bot])
    sched.add_job(run_checks, "date", args=[bot], run_date=datetime.now().astimezone())

    if config.DEALS_ENABLED and config.CHANNEL_ID:
        sched.add_job(run_deals, "interval", minutes=config.DEALS_INTERVAL_MIN, args=[bot])
        print(f"📢 Автопостинг включён → {config.CHANNEL_ID} "
              f"(раз в {config.DEALS_INTERVAL_MIN} мин)")
    sched.start()

    print(f"🤖 Бот @{BOT_USERNAME} запущен. Остановить — Ctrl+C.")
    try:
        await dp.start_polling(bot)
    finally:
        await aviasales.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        print(e if str(e) else "Остановлено.")
