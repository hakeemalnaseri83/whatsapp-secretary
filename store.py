"""SQLite storage for booking history."""
import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv("PROFILE_DB", "profiles.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def _db():
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        import psycopg
        conn = psycopg.connect(DATABASE_URL)
        conn.execute("""CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY, user_id TEXT NOT NULL,
            request TEXT NOT NULL, restaurant_phone TEXT NOT NULL,
            profile TEXT NOT NULL, status TEXT NOT NULL, result TEXT DEFAULT '',
            created_at TEXT NOT NULL)""")
        conn.commit()
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        request TEXT NOT NULL, restaurant_phone TEXT NOT NULL,
        profile TEXT NOT NULL, status TEXT NOT NULL, result TEXT DEFAULT '',
        created_at TEXT NOT NULL)""")
    return conn


def create_reminder(user_id: str, request: str, remind_at: str) -> int:
    with _db() as conn:
        reminder_id = "SERIAL PRIMARY KEY" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "INTEGER PRIMARY KEY"
        conn.execute(f"CREATE TABLE IF NOT EXISTS reminders (id {reminder_id}, user_id TEXT NOT NULL, request TEXT NOT NULL, remind_at TEXT NOT NULL, sent INTEGER DEFAULT 0)")
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        if DATABASE_URL.startswith(("postgres://", "postgresql://")):
            cur = conn.execute("INSERT INTO reminders(user_id,request,remind_at,sent) VALUES (%s,%s,%s,0) RETURNING id", (user_id, request, remind_at))
            rid = cur.fetchone()[0]
        else:
            cur = conn.execute("INSERT INTO reminders(user_id,request,remind_at,sent) VALUES (?,?,?,0)", (user_id, request, remind_at))
            rid = cur.lastrowid
        conn.commit()
        return int(rid)


def due_reminders(now: str) -> list[tuple]:
    with _db() as conn:
        reminder_id = "SERIAL PRIMARY KEY" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "INTEGER PRIMARY KEY"
        conn.execute(f"CREATE TABLE IF NOT EXISTS reminders (id {reminder_id}, user_id TEXT NOT NULL, request TEXT NOT NULL, remind_at TEXT NOT NULL, sent INTEGER DEFAULT 0)")
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        return conn.execute(f"SELECT id,user_id,request FROM reminders WHERE sent=0 AND remind_at <= {mark}", (now,)).fetchall()


def mark_reminder_sent(reminder_id: int) -> None:
    with _db() as conn:
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        conn.execute(f"UPDATE reminders SET sent=1 WHERE id={mark}", (reminder_id,))
        conn.commit()


def create_booking(booking: dict) -> int:
    with _db() as conn:
        if DATABASE_URL.startswith(("postgres://", "postgresql://")):
            cur = conn.execute(
                "INSERT INTO bookings(user_id,request,restaurant_phone,profile,status,created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (booking.get("owner", ""), booking.get("request", ""), booking["restaurant_phone"],
                 json.dumps(booking.get("profile", {}), ensure_ascii=False), "calling",
                 datetime.now(timezone.utc).isoformat()))
            return int(cur.fetchone()[0])
        cur = conn.execute(
            "INSERT INTO bookings(user_id,request,restaurant_phone,profile,status,created_at) VALUES (?,?,?,?,?,?)",
            (booking.get("owner", ""), booking.get("request", ""), booking["restaurant_phone"],
             json.dumps(booking.get("profile", {}), ensure_ascii=False), "calling",
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return int(cur.lastrowid)


def update_booking(booking_id: str, status: str, result: str) -> None:
    with _db() as conn:
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        conn.execute(f"UPDATE bookings SET status={mark}, result={mark} WHERE id={mark}", (status, result, booking_id))
        conn.commit()


def recent_bookings(user_id: str, limit: int = 5) -> list[tuple]:
    with _db() as conn:
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        return conn.execute(
            f"SELECT created_at, restaurant_phone, status, result FROM bookings WHERE user_id={mark} ORDER BY id DESC LIMIT {mark}",
            (user_id, limit)).fetchall()
