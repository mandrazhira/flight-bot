"""Работа с Aviasales / Travelpayouts (Оптимизированная версия для высоких нагрузок).

Основные улучшения:
1. Кэширование ответов API в памяти (с TTL, например, 15 минут) для избежания
   повторных запросов за одними и теми же месячными данными.
2. Семафор для ограничения одновременных запросов к Travelpayouts API
   (предотвращение ошибок 429 Too Many Requests и бан по IP).
3. Параллельное выполнение запросов через asyncio.gather вместо последовательных циклов.
4. Сохранение 100% исходной логики фильтрации и формирования партнерских ссылок.
"""

from datetime import date, timedelta
import asyncio
import time
import httpx

import config
import db

_client = httpx.AsyncClient(timeout=30.0)

DATA_CITIES = "https://api.travelpayouts.com/data/ru/cities.json"
AUTOCOMPLETE = "https://autocomplete.travelpayouts.com/places2"
PRICES = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
GROUPED = "https://api.travelpayouts.com/aviasales/v3/grouped_prices"

# Простой кэш в памяти: ключ -> (timestamp, data)
_api_cache = {}
CACHE_TTL = 900  # 15 минут

# Семафор для ограничения одновременных запросов к API Travelpayouts (максимум 5 параллельно)
_api_semaphore = asyncio.Semaphore(5)


def _get_from_cache(cache_key: str):
    if cache_key in _api_cache:
        ts, data = _api_cache[cache_key]
        if time.time() - ts < CACHE_TTL:
            return data
        else:
            del _api_cache[cache_key]
    return None


def _set_to_cache(cache_key: str, data):
    _api_cache[cache_key] = (time.time(), data)


async def load_reference_data(force=False):
    if not force and not db.cities_is_empty():
        return
    print("⏬ Загружаю справочник городов...")
    r = await _client.get(DATA_CITIES)
    r.raise_for_status()
    db.save_cities(r.json())
    print("✅ Справочник загружен.")


async def search_places(term: str, limit: int = 6):
    params = [("locale", "ru"), ("types[]", "city"), ("types[]", "airport"),
              ("term", term)]
    try:
        r = await _client.get(AUTOCOMPLETE, params=params)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("Ошибка автокомплита:", e)
        return []
    out = []
    for it in data[:limit]:
        if not it.get("code"):
            continue
        out.append({"type": it.get("type"), "code": it["code"],
                    "name": it.get("name", it["code"]),
                    "country_name": it.get("country_name", "")})
    return out


def _months_between(d1: date, d2: date):
    out, cur = [], date(d1.year, d1.month, 1)
    while cur <= d2:
        out.append(cur.strftime("%Y-%m"))
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return out


async def _fetch(origin, destination, departure_at, return_at=None, limit=1000,
                 direct=False, min_nights=None, max_nights=None):
    """Цена на КАЖДЫЙ день через grouped_prices (group_by=departure_at) с кэшированием и семафором."""
    cache_key = f"{origin}:{destination}:{departure_at}:{return_at or ''}:{direct}:{min_nights or ''}:{max_nights or ''}"
    cached = _get_from_cache(cache_key)
    if cached is not None:
        return cached

    params = {"origin": origin, "destination": destination,
              "departure_at": departure_at,
              "group_by": "departure_at",
              "direct": "true" if direct else "false",
              "currency": config.CURRENCY, "market": config.MARKET,
              "token": config.TP_TOKEN}
    if return_at:
        params["return_at"] = return_at
        if min_nights is not None:
            params["min_trip_duration"] = min_nights
            params["max_trip_duration"] = max_nights

    async with _api_semaphore:
        try:
            r = await _client.get(GROUPED, params=params)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            print(f"  ! ошибка API {origin}->{destination} {departure_at}: {e}")
            return []

    if not payload.get("success"):
        return []
    data = payload.get("data") or {}
    
    if isinstance(data, dict):
        result = list(data.values())
    else:
        result = data

    _set_to_cache(cache_key, result)
    return result


def _iso(v):
    v = (v or "")[:10]
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


async def collect_matches(origin: str, destination: str, date_from: str, date_to: str,
                          trip_type: str = "oneway",
                          min_nights: int = None, max_nights: int = None,
                          budget: int = None, direct: bool = False):
    """Собирает ВСЕ подходящие варианты по маршруту параллельно."""
    d1, d2 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    if budget is None:
        budget = config.MAX_REQUESTS_PER_ROUTE
    found = []
    seen = set()

    def consider(it):
        dep = _iso(it.get("departure_at"))
        price = it.get("price")
        if not dep or not price or not (d1 <= dep <= d2):
            return
        nights, ret_s = None, None
        if trip_type == "round":
            ret = _iso(it.get("return_at"))
            if not ret:
                return
            nights = (ret - dep).days
            if nights < min_nights or nights > max_nights:
                return
            ret_s = ret.isoformat()
        key = (dep.isoformat(), ret_s, price)
        if key in seen:
            return
        seen.add(key)
        found.append({"price": price, "departure_at": dep.isoformat(),
                      "return_at": ret_s, "nights": nights,
                      "airline": it.get("airline", ""),
                      "found_at": it.get("found_at", ""),
                      "link": it.get("link", "")})

    if trip_type == "oneway":
        months = _months_between(d1, d2)[:budget]
        tasks = [_fetch(origin, destination, month, direct=direct) for month in months]
        results = await asyncio.gather(*tasks)
        for res in results:
            for it in res:
                consider(it)
    else:
        om_list = _months_between(d1, d2)
        rm_list = _months_between(d1 + timedelta(days=min_nights),
                                 d2 + timedelta(days=max_nights))
        
        tasks = []
        for om in om_list:
            for rm in rm_list:
                if len(tasks) >= budget:
                    break
                tasks.append(_fetch(origin, destination, om, return_at=rm,
                                    direct=direct, min_nights=min_nights,
                                    max_nights=max_nights))
            if len(tasks) >= budget:
                break

        results = await asyncio.gather(*tasks)
        for res in results:
            for it in res:
                consider(it)

    if found:
        return found

    # запасной путь: по дням вылета (параллельно через asyncio.gather)
    cur = d1
    day_list = []
    while cur <= d2 and len(day_list) < budget:
        day_list.append(cur)
        cur += timedelta(days=1)

    if trip_type == "oneway":
        tasks = [_fetch(origin, destination, c.isoformat(), limit=1, direct=direct) for c in day_list]
        results = await asyncio.gather(*tasks)
        for res in results:
            for it in res:
                consider(it)
    else:
        tasks = []
        for c in day_list:
            for rm in _months_between(c + timedelta(days=min_nights),
                                      c + timedelta(days=max_nights)):
                if len(tasks) >= budget:
                    break
                tasks.append(_fetch(origin, destination, c.isoformat(),
                                    return_at=rm, direct=direct,
                                    min_nights=min_nights, max_nights=max_nights))
            if len(tasks) >= budget:
                break
        results = await asyncio.gather(*tasks)
        for res in results:
            for it in res:
                consider(it)

    return found


async def get_min_price(origin: str, destination: str, date_from: str, date_to: str,
                        trip_type: str = "oneway",
                        min_nights: int = None, max_nights: int = None,
                        direct: bool = False):
    """Самый дешёвый вариант по маршруту или None."""
    matches = await collect_matches(origin, destination, date_from, date_to,
                                    trip_type, min_nights, max_nights,
                                    direct=direct)
    if not matches:
        return None
    return min(matches, key=lambda x: x["price"])


def link_from_api(api_link: str) -> str:
    if not api_link:
        return ""
    url = "https://www.aviasales.ru" + api_link
    sep = "&" if "?" in api_link else "?"
    if config.TP_MARKER:
        url += f"{sep}marker={config.TP_MARKER}"
    return url


def build_link(origin: str, destination: str, depart_date: str,
               return_date: str = None, direct: bool = False,
               api_link: str = None) -> str:
    if api_link:
        ready = link_from_api(api_link)
        if ready:
            return ready

    if len(depart_date) == 7:
        depart_date += "-01"
    params = [
        f"origin_iata={origin}",
        f"destination_iata={destination}",
        f"depart_date={depart_date}",
    ]
    if return_date:
        params.append(f"return_date={return_date}")
        params.append("oneway=0")
    else:
        params.append("oneway=1")
    params += [
        "adults=1", "children=0", "infants=0", "trip_class=0",
        f"currency={config.CURRENCY.upper()}",
        "locale=ru",
        "with_request=true",
    ]
    if direct:
        params.append("direct=true")
    if config.TP_MARKER:
        params.append(f"marker={config.TP_MARKER}")
    return "https://search.aviasales.com/flights/?" + "&".join(params)


async def aclose():
    await _client.aclose()
