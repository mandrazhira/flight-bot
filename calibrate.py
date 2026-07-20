"""КАЛИБРОВЩИК ПОРОГОВ для канала.

Зачем: пороги в deals.json решают, что считать "находкой" и постить в канал.
Если поставить их наугад — канал будет либо спамить, либо молчать.

Этот скрипт спрашивает РЕАЛЬНЫЕ цены у того же API, который использует бот,
смотрит на распределение цен по каждому маршруту и предлагает разумный порог.

Запуск:
    python calibrate.py            — показать предложения, ничего не менять
    python calibrate.py --save     — записать новые пороги в deals.json
                                     (старый файл сохранится как deals.json.bak)

Как считается порог:
  берётся 15-й процентиль всех найденных цен — то есть уровень, дешевле
  которого оказывается примерно каждый седьмой билет. Так канал молчит
  в обычные дни и оживает, когда цена реально просела.
"""
import asyncio
import json
import shutil
import sys
from datetime import date, timedelta

import aviasales
import config
import db

PERCENTILE = 15          # какой процентиль считать "находкой"
ROUND_TO = 500           # округлять порог до ближайших N рублей
BUDGET = 30              # запросов к API на маршрут (разово, не страшно)


def percentile(sorted_vals, pct):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def money(n):
    return f"{int(n):,}".replace(",", " ")


async def main():
    save = "--save" in sys.argv
    config.check_required()
    db.init_db()

    with open(config.DEALS_FILE, encoding="utf-8") as f:
        deals = json.load(f)

    today = date.today()
    date_from = today.isoformat()
    date_to = (today + timedelta(days=config.DEALS_LOOKAHEAD_DAYS)).isoformat()

    print(f"\nСмотрю реальные цены на {config.DEALS_LOOKAHEAD_DAYS} дней вперёд "
          f"({date_from} → {date_to}).")
    print("Это займёт пару минут — иду по каждому маршруту.\n")
    print(f"{'Маршрут':<34} {'найдено':>8} {'мин':>9} {'средняя':>9} "
          f"{'было':>9} {'станет':>9}")
    print("-" * 84)

    changed = 0
    for d in deals:
        trip_type = d.get("type", "round")
        nights = d.get("nights", [5, 10])
        route = f"{d['origin_name']} → {d['dest_name']}"
        try:
            matches = await aviasales.collect_matches(
                d["origin"], d["dest"], date_from, date_to,
                trip_type, nights[0], nights[1], budget=BUDGET)
        except Exception as e:
            print(f"{route:<34} ошибка: {e}")
            continue
        await asyncio.sleep(1)

        prices = sorted(m["price"] for m in matches if m.get("price"))
        if len(prices) < 5:
            print(f"{route:<34} {len(prices):>8} — мало данных, порог не трогаю")
            continue

        p = percentile(prices, PERCENTILE)
        suggested = int(round(p / ROUND_TO) * ROUND_TO)
        avg = sum(prices) / len(prices)
        old = d["threshold"]
        mark = "" if suggested == old else "  ←"
        print(f"{route:<34} {len(prices):>8} {money(prices[0]):>9} "
              f"{money(avg):>9} {money(old):>9} {money(suggested):>9}{mark}")
        d["threshold"] = suggested
        if suggested != old:
            changed += 1

    print("-" * 84)
    if save:
        shutil.copy(config.DEALS_FILE, config.DEALS_FILE + ".bak")
        with open(config.DEALS_FILE, "w", encoding="utf-8") as f:
            json.dump(deals, f, ensure_ascii=False, indent=2)
        print(f"\n✅ Записал новые пороги в {config.DEALS_FILE}")
        print(f"   Изменено маршрутов: {changed}")
        print(f"   Старый файл сохранён как {config.DEALS_FILE}.bak")
        print("\nТеперь можно включать DEALS_ENABLED=true и перезапускать бота.")
    else:
        print("\nЭто только предложение — файл не тронут.")
        print("Чтобы записать эти пороги:  python calibrate.py --save")

    await aviasales.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        print(e if str(e) else "Остановлено.")
