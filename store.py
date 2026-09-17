"""SQLite storage for booking history."""
import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.getenv("PROFILE_DB", "profiles.sqlite3")


def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
        request TEXT NOT NULL, restaurant_phone TEXT NOT NULL,
        profile TEXT NOT NULL, status TEXT NOT NULL, result TEXT DEFAULT '',
        created_at TEXT NOT NULL)""")
    return conn


def create_booking(booking: dict) -> int:
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO bookings(user_id,request,restaurant_phone,profile,status,created_at) VALUES (?,?,?,?,?,?)",
            (booking.get("owner", ""), booking.get("request", ""), booking["restaurant_phone"],
             json.dumps(booking.get("profile", {}), ensure_ascii=False), "calling",
             datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return int(cur.lastrowid)


def update_booking(booking_id: str, status: str, result: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE bookings SET status=?, result=? WHERE id=?", (status, result, booking_id))
        conn.commit()


def recent_bookings(user_id: str, limit: int = 5) -> list[tuple]:
    with _db() as conn:
        return conn.execute(
            "SELECT created_at, restaurant_phone, status, result FROM bookings WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)).fetchall()
