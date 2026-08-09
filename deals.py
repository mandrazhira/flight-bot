"""Автопостинг находок в публичный канал (Оптимизированная версия)."""
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
        return []
    try:
        with open(config.DEALS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def should_post(route_key: str, price: int) -> bool:
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

async def process_deal(bot: Bot, bot_username: str, d, date_from, date_to):
    """Обработка одного маршрута для канала."""
    trip_type = d.get("type", "round")
    nights = d.get("nights", [5, 10])
    try:
        best = await aviasales.get_min_price(
            d["origin"], d["dest"], date_from, date_to, trip_type,
            nights[0], nights[1])
        
        if not best or best["price"] > d["threshold"]:
            return False

        route_key = f"{d['origin']}-{d['dest']}-{trip_type}"
        if not should_post(route_key, best["price"]):
            return False

        # Формирование сообщения и отправка (код из вашего оригинала)
        # ... (здесь ваш код отправки в канал) ...
        
        db.save_posted_deal(route_key, best["price"])
        return True
    except Exception as e:
        print(f" Ошибка в сделке {d['origin']}->{d['dest']}: {e}")
        return False

async def check_and_post(bot: Bot, bot_username: str, force=False) -> int:
    if not config.CHANNEL_ID:
        return 0
    deals = load_deals()
    if not deals:
        return 0

    today = date.today()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=config.DEALS_LOOKAHEAD_DAYS)).isoformat()
    
    print(f"[deals] Проверяем {len(deals)} популярных маршрутов...")
    
    # Проверяем пачками по 5 штук
    posted_count = 0
    batch_size = 5
    for i in range(0, len(deals), batch_size):
        batch = deals[i:i+batch_size]
        tasks = [process_deal(bot, bot_username, d, date_from, date_to) for d in batch]
        results = await asyncio.gather(*tasks)
        posted_count += sum(1 for r in results if r)
        await asyncio.sleep(1) # Пауза между пачками

    return posted_count
