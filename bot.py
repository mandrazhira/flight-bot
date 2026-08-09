"""Телеграм-бот (Оптимизированная версия для 1000+ подписок)."""
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
BOT_USERNAME = ""

def money(n) -> str:
    return f"{n:,}".replace(",", " ")

def fmt_ru(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}.{d.year}"

async def stale(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("Кнопка устарела", show_alert=True)
    await call.message.answer("⏳ Эта кнопка устарела. Начни заново: /track")

def with_cancel(kb: InlineKeyboardBuilder, cols=1) -> InlineKeyboardMarkup:
    kb.button(text="❌ Начать заново", callback_data="cancel")
    kb.adjust(cols)
    return kb.as_markup()

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✈️ Новый поиск")],
        [KeyboardButton(text="📋 Мои отслеживания"), KeyboardButton(text="❓ Помощь")],
    ],
    resize_keyboard=True)

HELP_TEXT = "..." # (сокращено для краткости, оставьте ваш текст)

class Track(StatesGroup):
    origin = State()
    dests = State()
    trip_type = State()
    direct = State()
    dates = State()
    nights = State()
    price = State()

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("✈️ Привет! Я слежу за ценами на авиабилеты.", reply_markup=MAIN_KB)

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменил.")

@router.callback_query(F.data == "cancel")
async def cb_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.answer("❌ Начинаем заново. Откуда летим?")
    await state.set_state(Track.origin)

@router.message(Track.origin)
async def track_origin(message: Message, state: FSMContext):
    results = await aviasales.search_places(message.text.strip())
    if not results:
        await message.answer("Ничего не нашёл 🤔")
        return
    await state.update_data(origin_results=results)
    kb = InlineKeyboardBuilder()
    for i, p in enumerate(results):
        kb.button(text=p["name"], callback_data=f"org:{i}")
    await message.answer("Выбери пункт вылета:", reply_markup=with_cancel(kb))

# ... (Остальные обработчики шагов оставьте как есть) ...

# ================= ФОНОВЫЙ МОНИТОРИНГ (ОПТИМИЗИРОВАН) =================

async def find_best(s, cache=None):
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
        except Exception as e:
            print(f"  ! ошибка проверки {s['origin_code']}->{d['code']}: {e}")
            continue
        if res and res.get("price") and (best is None or res["price"] < best["price"]):
            best = {**res, "dest_code": d["code"], "dest_name": d["name"]}
    return best

async def check_sub(bot: Bot, s, cache=None):
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
    """Проверка всех подписок пачками (Batch Processing)."""
    db.delete_expired(date.today().isoformat())
    subs = db.all_active_subscriptions()
    if not subs:
        return
    
    print(f"[monitor] Стартуем проверку {len(subs)} подписок...")
    cache = {}
    
    # Проверяем пачками по 10 штук, чтобы не забивать память и API
    batch_size = 10
    for i in range(0, len(subs), batch_size):
        batch = subs[i:i + batch_size]
        tasks = [check_sub(bot, s, cache) for s in batch]
        await asyncio.gather(*tasks)
        # Небольшая пауза между пачками для стабильности
        await asyncio.sleep(0.5)
    
    print(f"[monitor] Проверка завершена.")

async def send_signal(bot: Bot, sub, best):
    # (Ваш код отправки сигнала без изменений)
    pass

async def main():
    # (Ваш основной цикл без изменений)
    pass

if __name__ == "__main__":
    asyncio.run(main())
