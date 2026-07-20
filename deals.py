"""Автопостинг находок в публичный канал.

Зачем: канал с реальными дешёвыми билетами — главный источник новых
пользователей бота. Бот сам находит цены → сам постит → люди приходят
настраивать свои маршруты.

Список отслеживаемых маршрутов — в файле deals.json (можно править руками).
Каждый маршрут: откуда, куда, тип, длительность, порог "дешевизны".
"""
import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import aviasales
import config
import db

MONTHS_GEN = ["янв", "фев", "мар", "апр", "мая", "июн",
              "июл", "авг", "сен", "окт", "ноя", "дек"]


def money(n) -> str:
    return f"{n:,}".replace(",", " ")


def short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def nights_word(n: int) -> str:
    if 11 <= n % 100 <= 14:
        return "ночей"
    return {1: "ночь", 2: "ночи", 3: "ночи", 4: "ночи"}.get(n % 10, "ночей")


def load_deals():
    if not os.path.exists(config.DEALS_FILE):
        print(f"⚠️  Файл {config.DEALS_FILE} не найден — автопостинг пропущен.")
        return []
    try:
        with open(config.DEALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Не смог прочитать {config.DEALS_FILE}: {e}")
        return []


def should_post(route_key: str, price: int) -> bool:
    """Не спамим: повторяем пост, только если цена заметно упала
    или прошло достаточно дней."""
    prev = db.get_posted_deal(route_key)
    if prev is None:
        return True
    if price <= prev["price"] * (1 - config.DEALS_REPOST_DROP_PCT / 100):
        return True
    try:
        posted = datetime.fromisoformat(prev["posted_at"])
    except ValueError:
        return True
    age = datetime.now(timezone.utc) - posted
    return age > timedelta(days=config.DEALS_REPOST_DAYS) and price <= prev["price"]


async def check_and_post(bot: Bot, bot_username: str, force=False) -> int:
    """Один проход по deals.json. Возвращает число опубликованных постов."""
    if not config.CHANNEL_ID:
        return 0
    deals = load_deals()
    if not deals:
        return 0

    today = date.today()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=config.DEALS_LOOKAHEAD_DAYS)).isoformat()
    posted = 0

    print(f"[deals] проверяю {len(deals)} популярных маршрутов...")
    for d in deals:
        trip_type = d.get("type", "round")
        nights = d.get("nights", [5, 10])
        try:
            best = await aviasales.get_min_price(
                d["origin"], d["dest"], date_from, date_to, trip_type,
                nights[0], nights[1])
        except Exception as e:
            print(f"  ! {d['origin']}->{d['dest']}: {e}")
            continue
        await asyncio.sleep(1)

        if not best or best["price"] > d["threshold"]:
            continue

        route_key = f"{d['origin']}-{d['dest']}-{trip_type}"
        if not force and not should_post(route_key, best["price"]):
            continue

        try:
            await post_deal(bot, bot_username, d, best, trip_type)
            db.save_posted_deal(route_key, best["price"])
            posted += 1
            await asyncio.sleep(3)   # пауза между постами в канал
        except Exception as e:
            print(f"  ! не смог опубликовать {route_key}: {e}")

    print(f"[deals] опубликовано постов: {posted}")
    return posted


async def post_deal(bot: Bot, bot_username: str, d, best, trip_type):
    link = aviasales.build_link(d["origin"], d["dest"], best["departure_at"],
                                best.get("return_at"),
                                api_link=best.get("link"))
    if trip_type == "round":
        kind = "туда-обратно"
        when = (f"📅 {short_date(best['departure_at'])} → "
                f"{short_date(best['return_at'])} "
                f"({best['nights']} {nights_word(best['nights'])})")
    else:
        kind = "в одну сторону"
        when = f"📅 вылет {short_date(best['departure_at'])}"

    text = (f"🔥 <b>{d['origin_name']} → {d['dest_name']}</b>\n\n"
            f"💰 <b>{money(best['price'])} ₽</b> {kind}\n"
            f"{when}\n\n"
            f"⚠️ Цена из кэша — проверь актуальную по кнопке, "
            f"дешёвые тарифы разбирают быстро.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Смотреть билет", url=link)],
        [InlineKeyboardButton(text="🔔 Следить за своим маршрутом",
                              url=f"https://t.me/{bot_username}?start=deal")],
    ])
    await bot.send_message(config.CHANNEL_ID, text, reply_markup=kb,
                           disable_web_page_preview=True)
