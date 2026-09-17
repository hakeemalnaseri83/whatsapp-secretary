"""Small local profile store for WhatsApp users."""
import json
import os
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("PROFILE_DB", "profiles.sqlite3")
DATABASE_URL = os.getenv("DATABASE_URL", "")


def _connect():
    if DATABASE_URL.startswith(("postgres://", "postgresql://")):
        import psycopg
        conn = psycopg.connect(DATABASE_URL)
        conn.execute("CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        conn.commit()
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    return conn


def get_profile(user_id: str) -> dict:
    with closing(_connect()) as conn:
        mark = "%s" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "?"
        row = conn.execute(f"SELECT data FROM profiles WHERE user_id={mark}", (user_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def update_profile(user_id: str, **values) -> dict:
    profile = get_profile(user_id)
    profile.update({k: v.strip() for k, v in values.items() if v and v.strip()})
    with closing(_connect()) as conn:
        data = json.dumps(profile, ensure_ascii=False)
        if DATABASE_URL.startswith(("postgres://", "postgresql://")):
            conn.execute("INSERT INTO profiles(user_id,data) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET data=EXCLUDED.data", (user_id, data))
        else:
            conn.execute("INSERT OR REPLACE INTO profiles(user_id,data) VALUES (?,?)", (user_id, data))
        conn.commit()
    return profile


def profile_reply(profile: dict) -> str:
    lines = ["تم حفظ بياناتك الأساسية:"]
    for key, label in (("name", "الاسم"), ("job", "العمل"), ("address", "العنوان")):
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    return "\n".join(lines)
