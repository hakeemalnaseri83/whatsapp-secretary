"""Small local profile store for WhatsApp users."""
import json
import os
import sqlite3
from contextlib import closing

DB_PATH = os.getenv("PROFILE_DB", "profiles.sqlite3")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS profiles (user_id TEXT PRIMARY KEY, data TEXT NOT NULL)")
    return conn


def get_profile(user_id: str) -> dict:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT data FROM profiles WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def update_profile(user_id: str, **values) -> dict:
    profile = get_profile(user_id)
    profile.update({k: v.strip() for k, v in values.items() if v and v.strip()})
    with closing(_connect()) as conn:
        conn.execute("INSERT OR REPLACE INTO profiles(user_id,data) VALUES (?,?)",
                     (user_id, json.dumps(profile, ensure_ascii=False)))
        conn.commit()
    return profile


def profile_reply(profile: dict) -> str:
    lines = ["تم حفظ بياناتك الأساسية:"]
    for key, label in (("name", "الاسم"), ("job", "العمل"), ("address", "العنوان")):
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    return "\n".join(lines)
