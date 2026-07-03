import sqlite3
import json
import time
from typing import List, Dict, Optional


class ChatRepository:
    @staticmethod
    def get_chats_for_user(db: sqlite3.Connection, user_email: str) -> List[Dict]:
        c = db.cursor()
        c.execute("SELECT id, title, messages_json FROM chats WHERE user_email=? ORDER BY updated_at ASC", (user_email,))
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
            chats_array.append({
                "id": r['id'],
                "title": r['title'],
                "ms": ms
            })
        return chats_array

    @staticmethod
    def sync_user_chats(db: sqlite3.Connection, user_email: str, chats: List[dict]):
        MAX_CHATS = 200
        MAX_MESSAGES_PER_CHAT = 500
        MAX_MESSAGE_CHARS = 120_000
        if not isinstance(chats, list):
            raise ValueError("Chat sync payload must be a list.")
        if len(chats) > MAX_CHATS:
            raise ValueError(f"Too many chats to sync ({len(chats)}). Maximum is {MAX_CHATS}.")

        now = time.time()
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

            updated_at = chat.get('updated_at') or chat.get('updatedAt') or now
            try:
                updated_at = float(updated_at)
            except (TypeError, ValueError):
                updated_at = now

            payload = json.dumps(cleaned_messages)
            c.execute(
                """
                INSERT INTO chats (id, user_email, title, messages_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    messages_json=excluded.messages_json,
                    updated_at=MAX(chats.updated_at, excluded.updated_at)
                WHERE chats.user_email=excluded.user_email
                """,
                (cid, user_email, title, payload, updated_at),
            )
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
