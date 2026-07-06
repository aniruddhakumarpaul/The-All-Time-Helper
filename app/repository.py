import sqlite3
import json
import time
from typing import List, Dict, Optional


CHAT_TIMESTAMP_MILLISECONDS_THRESHOLD = 100_000_000_000
CHAT_TIMESTAMP_SECONDS_FLOOR = 1_000_000_000


def normalize_chat_timestamp(value, default=None) -> float:
    fallback = time.time() * 1000 if default is None else float(default)
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return fallback
    if CHAT_TIMESTAMP_SECONDS_FLOOR <= timestamp < CHAT_TIMESTAMP_MILLISECONDS_THRESHOLD:
        timestamp *= 1000
    return timestamp


class ChatRepository:
    @staticmethod
    def get_chats_for_user(db: sqlite3.Connection, user_email: str) -> List[Dict]:
        c = db.cursor()
        c.execute("SELECT id, title, messages_json, updated_at FROM chats WHERE user_email=? ORDER BY updated_at ASC", (user_email,))
        rows = c.fetchall()
        chats_array = []
        for r in rows:
            ms = []
            if r['messages_json']:
                try:
                    parsed = json.loads(r['messages_json'])
                    ms = parsed if isinstance(parsed, list) else []
                except Exception:
                    ms = []
            updated_at = normalize_chat_timestamp(r['updated_at'] or 0, default=0)
            chats_array.append({
                "id": r['id'],
                "title": r['title'],
                "ms": ms,
                "updated_at": updated_at,
                "updatedAt": updated_at,
            })
        return chats_array

    @staticmethod
    def sync_user_chats(db: sqlite3.Connection, user_email: str, payload):
        MAX_CHATS = 200
        MAX_MESSAGES_PER_CHAT = 500
        MAX_MESSAGE_CHARS = 120_000
        if isinstance(payload, dict):
            chats = payload.get("chats", [])
            deleted_chat_ids = payload.get("deleted_chat_ids", [])
        else:
            chats = payload
            deleted_chat_ids = []
        if not isinstance(chats, list) or not isinstance(deleted_chat_ids, list):
            raise ValueError("Chat sync payload must contain chat and deletion lists.")
        if len(chats) > MAX_CHATS:
            raise ValueError(f"Too many chats to sync ({len(chats)}). Maximum is {MAX_CHATS}.")

        now = time.time() * 1000
        c = db.cursor()
        for chat in chats:
            if not isinstance(chat, dict):
                continue
            cid = chat.get('id')
            if not cid or not isinstance(cid, str):
                continue
            cid = cid[:120]
            title = str(chat.get('title', 'New Chat'))[:200]
            ms = chat.get('ms', [])
            if not isinstance(ms, list):
                ms = []
            if len(ms) > MAX_MESSAGES_PER_CHAT:
                ms = ms[-MAX_MESSAGES_PER_CHAT:]

            cleaned_messages = []
            for msg in ms:
                if not isinstance(msg, dict):
                    continue
                cleaned = dict(msg)
                if 'c' in cleaned and isinstance(cleaned['c'], str) and len(cleaned['c']) > MAX_MESSAGE_CHARS:
                    cleaned['c'] = cleaned['c'][:MAX_MESSAGE_CHARS]
                cleaned_messages.append(cleaned)

            updated_at = normalize_chat_timestamp(
                chat.get('updated_at') or chat.get('updatedAt'),
                default=now,
            )

            messages_json = json.dumps(cleaned_messages)
            c.execute(
                """
                INSERT INTO chats (id, user_email, title, messages_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    messages_json=excluded.messages_json,
                    updated_at=excluded.updated_at
                WHERE chats.user_email=excluded.user_email
                    AND excluded.updated_at >= chats.updated_at
                """,
                (cid, user_email, title, messages_json, updated_at),
            )
        for chat_id in deleted_chat_ids[:MAX_CHATS]:
            if isinstance(chat_id, str) and chat_id:
                c.execute("DELETE FROM chats WHERE id = ? AND user_email = ?", (chat_id[:120], user_email))
        db.commit()


class UserRepository:
    @staticmethod
    def get_user_by_email(db: sqlite3.Connection, email: str) -> Optional[sqlite3.Row]:
        c = db.cursor()
        c.execute("SELECT email, hashed_password, verified, name, otp, otp_expiry FROM users WHERE email=?", (email,))
        return c.fetchone()

    @staticmethod
    def create_user(db: sqlite3.Connection, email: str, hashed_pwd: str, name: str, otp: str, expiry: float):
        c = db.cursor()
        c.execute("INSERT INTO users (email, hashed_password, verified, otp, otp_expiry, name) VALUES (?, ?, 0, ?, ?, ?)",
                  (email, hashed_pwd, otp, expiry, name))
        db.commit()

    @staticmethod
    def update_otp(db: sqlite3.Connection, email: str, otp: str, expiry: float):
        c = db.cursor()
        c.execute("UPDATE users SET otp=?, otp_expiry=? WHERE email=?", (otp, expiry, email))
        db.commit()

    @staticmethod
    def verify_user(db: sqlite3.Connection, email: str):
        c = db.cursor()
        c.execute("UPDATE users SET verified=1, otp=NULL, otp_expiry=NULL WHERE email=?", (email,))
        db.commit()
