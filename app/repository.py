import sqlite3
import json
import time
from typing import List, Dict, Optional, Tuple

class ChatRepository:
    MAX_CHATS = 200
    MAX_MESSAGES_PER_CHAT = 500

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
                    ms = json.loads(r['messages_json'])
                except:
                    pass
            chats_array.append({
                "id": r['id'],
                "title": r['title'],
                "ms": ms,
                "updated_at": r['updated_at'] or 0,
                "updatedAt": r['updated_at'] or 0
            })
        return chats_array

    @staticmethod
    def parse_sync_payload(payload) -> Tuple[List[dict], List[str]]:
        if isinstance(payload, list):
            return payload, []
        if isinstance(payload, dict):
            chats = payload.get("chats", [])
            deleted_chat_ids = payload.get("deleted_chat_ids", [])
            if not isinstance(chats, list):
                raise ValueError("Invalid chat sync payload: 'chats' must be a list.")
            if not isinstance(deleted_chat_ids, list):
                raise ValueError("Invalid chat sync payload: 'deleted_chat_ids' must be a list.")
            return chats, [cid for cid in deleted_chat_ids if isinstance(cid, str)]
        raise ValueError("Invalid chat sync payload.")

    @staticmethod
    def _chat_timestamp(chat: dict) -> float:
        raw = chat.get("updatedAt", chat.get("updated_at"))
        try:
            ts = float(raw)
            return ts if ts > 0 else time.time()
        except (TypeError, ValueError):
            return time.time()

    @staticmethod
    def sync_user_chats(db: sqlite3.Connection, user_email: str, payload):
        chats, deleted_chat_ids = ChatRepository.parse_sync_payload(payload)
        if len(chats) > ChatRepository.MAX_CHATS:
            raise ValueError(f"Too many chats to sync ({len(chats)}). Maximum is {ChatRepository.MAX_CHATS}.")

        c = db.cursor()
        for cid in deleted_chat_ids:
            c.execute("DELETE FROM chats WHERE user_email=? AND id=?", (user_email, cid))

        for chat in chats:
            cid = chat.get('id')
            if not cid or not isinstance(cid, str):
                continue  # Skip malformed entries
            title = chat.get('title', 'New Chat')[:200]  # Cap title length
            ms = chat.get('ms', [])
            if not isinstance(ms, list):
                ms = []
            if len(ms) > ChatRepository.MAX_MESSAGES_PER_CHAT:
                ms = ms[-ChatRepository.MAX_MESSAGES_PER_CHAT:]  # Keep only recent messages

            incoming_ts = ChatRepository._chat_timestamp(chat)
            c.execute("SELECT updated_at FROM chats WHERE user_email=? AND id=?", (user_email, cid))
            existing = c.fetchone()
            existing_ts = float(existing["updated_at"] or 0) if existing else 0
            if existing and existing_ts > incoming_ts:
                continue

            c.execute(
                """
                INSERT INTO chats (id, user_email, title, messages_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_email=excluded.user_email,
                    title=excluded.title,
                    messages_json=excluded.messages_json,
                    updated_at=excluded.updated_at
                WHERE chats.user_email=excluded.user_email
                  AND COALESCE(chats.updated_at, 0) <= excluded.updated_at
                """,
                (cid, user_email, title, json.dumps(ms), incoming_ts)
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
        c.execute("UPDATE users SET verified=1 WHERE email=?", (email,))
        db.commit()
