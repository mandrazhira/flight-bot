"""Настройки бота.

Читает файл .env, который лежит РЯДОМ с этим файлом.
Терпим к тому, как файл назвали при скачивании: .env, .env.txt, env.txt, env —
подхватит любой вариант и скажет, если имя неправильное.
"""
import os
import sys

from dotenv import load_dotenv

# папка, где лежит сам бот (а не откуда его запустили)
HERE = os.path.dirname(os.path.abspath(__file__))

# возможные имена — Windows/Блокнот/браузер любят дописывать .txt или съедать точку
CANDIDATES = [".env", ".env.txt", "env.txt", "env", "_env", ".env.example"]


def _find_env():
    for name in CANDIDATES:
        path = os.path.join(HERE, name)
        if os.path.isfile(path):
            return path, name
    return None, None


_path, _name = _find_env()

if _path:
    load_dotenv(_path, override=True)
    if _name != ".env":
        print(f"\n⚠️  Нашёл настройки в файле '{_name}' (правильное имя — '.env').")
        print("    Работать буду, но лучше переименуй.\n")
else:
    print("=" * 58)
    print("❌ НЕ НАШЁЛ ФАЙЛ НАСТРОЕК")
    print("=" * 58)
    print(f"\nИскал в папке:\n   {HERE}\n")
    try:
        print("Что лежит в этой папке:")
        for f in sorted(os.listdir(HERE)):
            if not f.startswith("__"):
                print(f"   {f}")
    except Exception:
        pass
    print("\nЧТО ДЕЛАТЬ: положи файл .env в папку выше,")
    print("либо запусти:  python setup.py")
    print("=" * 58)
    sys.exit(1)


def _clean(name, default=""):
    return os.getenv(name, default).strip().strip('"').strip("'").strip()


# --- обязательные ---
BOT_TOKEN = _clean("BOT_TOKEN")
TP_TOKEN = _clean("TP_TOKEN")
TP_MARKER = _clean("TP_MARKER")

# --- канал для автопостинга ---
CHANNEL_ID = _clean("CHANNEL_ID")
ADMIN_ID = int(_clean("ADMIN_ID", "0") or 0)

# --- общие настройки ---
CHECK_INTERVAL_MIN = int(_clean("CHECK_INTERVAL_MIN", "60"))
CURRENCY = _clean("CURRENCY", "rub")
MARKET = _clean("MARKET", "ru")
MAX_DEST_CITIES = int(_clean("MAX_DEST_CITIES", "3"))
MAX_WINDOW_DAYS = int(_clean("MAX_WINDOW_DAYS", "30"))
MAX_NIGHTS = int(_clean("MAX_NIGHTS", "60"))
MAX_REQUESTS_PER_ROUTE = int(_clean("MAX_REQUESTS_PER_ROUTE", "12"))
DB_PATH = _clean("DB_PATH", "flightbot.db")

# --- автопостинг ---
DEALS_ENABLED = _clean("DEALS_ENABLED", "false").lower() in ("1", "true", "yes")
DEALS_INTERVAL_MIN = int(_clean("DEALS_INTERVAL_MIN", "180"))
DEALS_LOOKAHEAD_DAYS = int(_clean("DEALS_LOOKAHEAD_DAYS", "90"))
DEALS_REPOST_DROP_PCT = int(_clean("DEALS_REPOST_DROP_PCT", "7"))
DEALS_REPOST_DAYS = int(_clean("DEALS_REPOST_DAYS", "3"))
DEALS_FILE = os.path.join(HERE, _clean("DEALS_FILE", "deals.json"))

# база — рядом с ботом, чтобы не терялась при запуске из другой папки
if not os.path.isabs(DB_PATH) and DB_PATH != ":memory:":
    DB_PATH = os.path.join(HERE, DB_PATH)


def check_required():
    """Проверяет ключи и объясняет по-человечески, что не так."""
    problems = []
    for key, val in (("BOT_TOKEN", BOT_TOKEN), ("TP_TOKEN", TP_TOKEN)):
        if not val:
            problems.append(f"{key} — пусто, вставь значение после знака =")
        elif val.startswith("сюда"):
            problems.append(f"{key} — там осталась заглушка, замени на свой ключ")

    if problems:
        print("=" * 58)
        print("❌ КЛЮЧИ НЕ ЗАПОЛНЕНЫ")
        print("=" * 58)
        print(f"\nФайл я нашёл и читаю вот этот:\n   {_path}\n")
        print("Проблемы:")
        for p in problems:
            print(f"   • {p}")
        print("\nОткрой ИМЕННО ЭТОТ файл и заполни. Должно быть так:")
        print("   BOT_TOKEN=123456789:AAEabcdef...")
        print("   TP_TOKEN=длинная_строка")
        print("   TP_MARKER=12345")
        print("\nБез кавычек, без пробелов вокруг знака =")
        print("=" * 58)
        raise SystemExit(1)

    if not TP_MARKER or TP_MARKER.startswith("сюда"):
        print("⚠️  TP_MARKER не задан — бот работает, но комиссия с покупок "
              "тебе капать не будет.\n")
    if DEALS_ENABLED and not CHANNEL_ID:
        print("⚠️  DEALS_ENABLED=true, но CHANNEL_ID пуст — автопостинг выключен.\n")
