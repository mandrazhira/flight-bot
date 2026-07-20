"""База данных SQLite: справочник городов, подписки, история постов в канал."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import config

_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db():
    c = get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS cities (
            code TEXT PRIMARY KEY,
            name TEXT,
            country_code TEXT,
            flightable INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            origin_code TEXT NOT NULL,
            origin_name TEXT NOT NULL,
            dests TEXT NOT NULL,           -- JSON [{"code","name"}]
            date_from TEXT NOT NULL,       -- окно ВЫЛЕТА, ГГГГ-ММ-ДД
            date_to TEXT NOT NULL,
            trip_type TEXT DEFAULT 'oneway',   -- 'oneway' | 'round'
            min_nights INTEGER,            -- для 'round'
            max_nights INTEGER,
            direct INTEGER DEFAULT 0,      -- 1 = только прямые, 0 = любые
            max_price INTEGER NOT NULL,    -- для 'round' — за ВЕСЬ перелёт
            last_notified INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS posted_deals (
            route_key TEXT PRIMARY KEY,    -- 'MOW-IST-round'
            price INTEGER,
            posted_at TEXT
        );
        """
    )
    # мягкая миграция — если база осталась от старой версии
    cols = {r["name"] for r in c.execute("PRAGMA table_info(subscriptions)")}
    for name, ddl in (("trip_type", "TEXT DEFAULT 'oneway'"),
                      ("min_nights", "INTEGER"),
                      ("max_nights", "INTEGER"),
                      ("direct", "INTEGER DEFAULT 0")):
        if name not in cols:
            c.execute(f"ALTER TABLE subscriptions ADD COLUMN {name} {ddl}")
    c.commit()


# ---------- справочник ----------

def cities_is_empty() -> bool:
    return get_conn().execute("SELECT COUNT(*) AS n FROM cities").fetchone()["n"] == 0


def save_cities(items):
    rows = []
    for it in items:
        if not it.get("code"):
            continue
        rows.append((it["code"], it.get("name") or it["code"],
                     it.get("country_code", ""),
                     1 if it.get("has_flightable_airport", True) else 0))
    c = get_conn()
    c.executemany("INSERT OR REPLACE INTO cities(code, name, country_code, flightable) "
                  "VALUES (?, ?, ?, ?)", rows)
    c.commit()


# ---------- подписки ----------

def add_subscription(user_id, origin_code, origin_name, dests, date_from, date_to,
                     trip_type, min_nights, max_nights, max_price, direct=0) -> int:
    c = get_conn()
    cur = c.execute(
        "INSERT INTO subscriptions(user_id, origin_code, origin_name, dests, "
        "date_from, date_to, trip_type, min_nights, max_nights, max_price, "
        "direct, last_notified, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,?)",
        (user_id, origin_code, origin_name, json.dumps(dests, ensure_ascii=False),
         date_from, date_to, trip_type, min_nights, max_nights, max_price,
         int(direct), datetime.now(timezone.utc).isoformat()))
    c.commit()
    return cur.lastrowid


def parse_dests(row) -> list:
    return json.loads(row["dests"])


def get_subscription(sub_id):
    return get_conn().execute(
        "SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()


def list_subscriptions(user_id):
    return get_conn().execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()


def delete_subscription(sub_id, user_id) -> bool:
    c = get_conn()
    cur = c.execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?",
                    (sub_id, user_id))
    c.commit()
    return cur.rowcount > 0


def all_active_subscriptions():
    return get_conn().execute("SELECT * FROM subscriptions ORDER BY id").fetchall()


def set_last_notified(sub_id, price):
    c = get_conn()
    c.execute("UPDATE subscriptions SET last_notified = ? WHERE id = ?", (price, sub_id))
    c.commit()


def delete_expired(today_str: str) -> int:
    c = get_conn()
    cur = c.execute("DELETE FROM subscriptions WHERE date_to < ?", (today_str,))
    c.commit()
    return cur.rowcount


def count_users() -> int:
    return get_conn().execute(
        "SELECT COUNT(DISTINCT user_id) AS n FROM subscriptions").fetchone()["n"]


# ---------- история постов в канал ----------

def get_posted_deal(route_key: str):
    return get_conn().execute(
        "SELECT * FROM posted_deals WHERE route_key = ?", (route_key,)).fetchone()


def save_posted_deal(route_key: str, price: int):
    c = get_conn()
    c.execute("INSERT OR REPLACE INTO posted_deals(route_key, price, posted_at) "
              "VALUES (?, ?, ?)",
              (route_key, price, datetime.now(timezone.utc).isoformat()))
    c.commit()
